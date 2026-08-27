"""
F1 host replay harness (Phase 3).

Two modes:
  --offline   Build an ITCH replay stream, run the golden chain, and print the
              expected decision trace + latency. Runs anywhere — no F1. This is
              the reference the on-hardware run is checked against, and it
              exercises exactly the data the device will see.
  --device    (on an f2.6xlarge with the AFI loaded) push the framed ITCH stream
              byte-by-byte into the CL over the OCL AXI-Lite (BAR0), read the
              decisions + latency histogram back, then diff against the offline
              golden. Uses a pure-Python mmap of the AppPF BAR0 sysfs resource
              file — no AWS C SDK needed. Run under sudo (BAR0 mmap needs root).

Usage:
    python f1/host_replay.py --offline --n 2000 --seed 1
    sudo fpga-load-local-image -S 0 -I agfi-006b5fd42e5f1cb6e
    sudo python3 f1/host_replay.py --device --n 120 --seed 7        # on F2
    sudo python3 f1/host_replay.py --device --slice data/aapl_slice.bin --bar0 /sys/bus/pci/devices/<bdf>/resource0
"""

import argparse, sys, os, time

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..', 'sim'))
sys.path.insert(0, os.path.join(HERE, '..', 'host'))

import replay_gen
import phase2_golden

CLK_NS = 5  # 200 MHz

# AXI-Lite register map (see register_map.md)
REG_HIST_BASE = 0x000   # R  hist[0..63], 4 B each (0x000..0x0FC)
REG_LAST_LAT  = 0x100   # R  last_latency_cycles
REG_CLEAR     = 0x104   # W  bit0=1 clears histogram + last_latency
REG_ITCH_PUSH = 0x200   # W  [7:0]=byte, [8]=tlast  -> ingress FIFO -> s_axis
REG_STATUS    = 0x204   # R  bit0 ingress_full, bit1 empty, bit2 egress_overflow, [15:8] dec_count
REG_DEC_COUNT = 0x2FC   # R  decisions captured
REG_DEC_BASE  = 0x400   # R  decision[i]: +0x0 action(bit0), +0x4 price, +0x8 size


# ──────────────────────────── BAR0 MMIO (mmap) ──────────────────────────────
# The CL hangs entirely off the OCL AXI-Lite, exposed to the host as the AppPF
# BAR0 PCIe resource once the AFI is loaded (fpga-load-local-image). We mmap the
# sysfs resource file and do 32-bit aligned accesses — no AWS C SDK required.
# NOTE: struct (un)pack on an mmap issues a 4-byte memcpy; on x86-64 for an
# aligned 32-bit field this lowers to a single mov, which is what OCL needs.
AMZN_VENDOR = '0x1d0f'   # Amazon.com, Inc. (AWS FPGA AppPF/MgmtPF)


def _discover_bar0():
    """Best-effort: find the AppPF BAR0 resource file for an AWS FPGA slot.
    Returns a path or None. Prefer passing --bar0 explicitly if ambiguous."""
    import glob
    cands = []
    for dev in sorted(glob.glob('/sys/bus/pci/devices/*')):
        try:
            if open(os.path.join(dev, 'vendor')).read().strip().lower() != AMZN_VENDOR:
                continue
        except OSError:
            continue
        res0 = os.path.join(dev, 'resource0')
        if os.path.exists(res0) and os.path.getsize(res0) > 0:
            cands.append(res0)
    return cands


class Bar0:
    """mmap'd 32-bit MMIO window over an AppPF BAR0 resource file."""
    def __init__(self, path):
        import mmap, struct
        self._struct = struct
        self._fd = os.open(path, os.O_RDWR | os.O_SYNC)
        self.size = os.fstat(self._fd).st_size
        self._mm = mmap.mmap(self._fd, self.size, mmap.MAP_SHARED,
                             mmap.PROT_READ | mmap.PROT_WRITE)
        self.path = path

    def poke(self, off, val):
        self._struct.pack_into('<I', self._mm, off, val & 0xFFFFFFFF)

    def peek(self, off):
        return self._struct.unpack_from('<I', self._mm, off)[0]

    def close(self):
        try:
            self._mm.close()
        finally:
            os.close(self._fd)


def build_stream(args) -> bytes:
    if args.slice:
        return replay_gen.load_slice(args.slice)
    return replay_gen.build_synthetic(args.n, args.seed)


def run_offline(args):
    stream = build_stream(args)
    decisions, rejects, stats = phase2_golden.run(stream)
    print(f"=== offline golden (source={'slice:'+args.slice if args.slice else f'synthetic n={args.n} seed={args.seed}'}) ===")
    print(f"  messages : {stats['messages']}")
    print(f"  accepted : {stats['accepted']}   rejected : {stats['rejected']}")
    print(f"  final net position : {stats['final_position']} shares")
    print(f"  (tick-to-trade latency confirmed in sim: 41 cyc = {41*CLK_NS} ns)")
    print("  first 8 expected decisions:")
    for mi, a, p, sz in decisions[:8]:
        print(f"    msg#{mi:<5} {'BUY ' if a==0 else 'SELL'} price={p} size={sz}")
    return decisions


def split_framed(stream):
    """Split a length-prefixed ITCH stream into raw message payloads.
    Each message is a 2-byte big-endian length followed by that many bytes."""
    off, out = 0, []
    while off + 2 <= len(stream):
        n = int.from_bytes(stream[off:off + 2], 'big'); off += 2
        out.append(stream[off:off + n]); off += n
    return out


def _collapse(seq):
    """Collapse consecutive duplicate decisions (held-book cooldown re-fires)."""
    out = []
    for x in seq:
        if not out or out[-1] != x:
            out.append(x)
    return out


def run_device(args):
    """On-hardware path: drive the whole pipeline over BAR0 (OCL AXI-Lite).

    Mirrors sim/tb_cl_csr.py exactly: clear -> push framed ITCH byte-by-byte to
    0x200 (honoring ingress-full backpressure) -> read decisions from 0x2FC/0x400
    -> read latency histogram -> diff against the offline golden.
    """
    # 1. locate + map BAR0
    bar_path = args.bar0
    if not bar_path:
        cands = _discover_bar0()
        if not cands:
            print("ERROR: no AWS FPGA (vendor 0x1d0f) BAR0 found. Is the AFI loaded?\n"
                  "  sudo fpga-load-local-image -S 0 -I <agfi>\n"
                  "  then pass --bar0 /sys/bus/pci/devices/<bdf>/resource0",
                  file=sys.stderr)
            return 2
        if len(cands) > 1:
            print(f"WARNING: multiple Amazon BAR0 candidates {cands}; using {cands[0]}. "
                  f"Pass --bar0 to pick.", file=sys.stderr)
        bar_path = cands[0]
    try:
        bar = Bar0(bar_path)
    except OSError as e:
        print(f"ERROR opening {bar_path}: {e}\n  (need root: run under sudo)", file=sys.stderr)
        return 2
    print(f"=== device run (BAR0 = {bar_path}, {bar.size} bytes) ===")

    try:
        stream = build_stream(args)
        raws = split_framed(stream)

        # 2. clear histogram + last-latency
        bar.poke(REG_CLEAR, 1)

        # 3. push the framed ITCH stream byte-by-byte; respect ingress_full
        pushed = 0
        for raw in raws:
            for i, b in enumerate(raw):
                spins = 0
                while bar.peek(REG_STATUS) & 0x1:        # ingress_full backpressure
                    spins += 1
                    if spins > 1_000_000:
                        print("ERROR: ingress FIFO stuck full — aborting.", file=sys.stderr)
                        return 3
                last = 1 if i == len(raw) - 1 else 0
                bar.poke(REG_ITCH_PUSH, (last << 8) | b)
                pushed += 1
        time.sleep(0.01)   # let the final RESCAN + egress capture drain

        # 4. read decisions back from the egress capture
        count = bar.peek(REG_DEC_COUNT)
        max_fit = max(0, (bar.size - REG_DEC_BASE) // 16)
        if count > max_fit:
            print(f"WARNING: {count} decisions but BAR0 only holds {max_fit}; truncating read.",
                  file=sys.stderr)
            count = max_fit
        hw = []
        for i in range(count):
            a = bar.peek(REG_DEC_BASE + 16 * i + 0)
            p = bar.peek(REG_DEC_BASE + 16 * i + 4)
            s = bar.peek(REG_DEC_BASE + 16 * i + 8)
            hw.append((a & 0x1, p, s))

        # 5. latency: histogram (64 buckets) + last decision's latency
        hist = [bar.peek(REG_HIST_BASE + 4 * b) for b in range(64)]
        hist_sum = sum(hist)
        last_lat_cyc = bar.peek(REG_LAST_LAT)
        # headline latency = lowest populated bucket (isolated-tick regime)
        min_bucket = next((b for b, v in enumerate(hist) if v), None)
    finally:
        bar.close()

    # 6. golden reference + compare
    gdec, grej, gstats = phase2_golden.run(stream)
    golden = [(a, p, s) for _, a, p, s in gdec]
    hw_c, gold_c = _collapse(hw), _collapse(golden)
    match = hw_c == gold_c

    print(f"  messages pushed     : {len(raws)} ({pushed} bytes)")
    print(f"  decisions read back : {count}")
    print(f"  latency hist sum    : {hist_sum}")
    if last_lat_cyc:
        print(f"  last decision latency: {last_lat_cyc} cyc = {last_lat_cyc * CLK_NS} ns")
    if min_bucket is not None:
        print(f"  min latency bucket   : {min_bucket} cyc = {min_bucket * CLK_NS} ns")
    print(f"  first 5 hw decisions : {hw[:5]}")
    print(f"  golden(collapsed)={len(gold_c)}  hw(collapsed)={len(hw_c)}")
    print(f"  EQUIVALENCE vs golden: {'PASS' if match else 'MISMATCH'}")
    if not match:
        print(f"    hw  : {hw_c[:6]}", file=sys.stderr)
        print(f"    gold: {gold_c[:6]}", file=sys.stderr)
    return 0 if (match and count > 0 and hist_sum > 0) else 1


def main():
    ap = argparse.ArgumentParser(description="F1 host replay harness (Phase 3)")
    ap.add_argument('--offline', action='store_true', help="run golden only (no F1)")
    ap.add_argument('--device', action='store_true', help="run on F1 hardware")
    ap.add_argument('--n', type=int, default=2000)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--slice', type=str, default=None, help="real carved ITCH slice")
    ap.add_argument('--bar0', type=str, default=None,
                    help="path to AppPF BAR0 resource file (auto-discovered if omitted)")
    args = ap.parse_args()

    if args.device:
        sys.exit(run_device(args))
    else:
        run_offline(args)


if __name__ == '__main__':
    main()
