"""
Software tick-to-trade latency benchmark — the CPU baseline for the FPGA.

Times the *same* computation the hardware does, per message, on the CPU:
    order_book.apply -> snapshot -> strategy.evaluate -> risk.check
using the bit-identical golden chain that the RTL is verified against
(sim/phase2_golden.py). This is the fair, like-for-like competitor to the
FPGA's on-chip latency counter: same logic, same input stream, CPU vs fabric.

It reports the full latency distribution (min / median / p90 / p99 / max) and
the JITTER (stdev, max-min) — the dimension where the FPGA's fixed 205 ns / 0 ns
spread is most striking next to a CPU's caches, branch prediction, interrupts
and OS scheduling.

Honest scoping: ITCH byte parsing is done once up front and excluded from the
per-message timing (the messages are pre-parsed), so this number is, if
anything, OPTIMISTIC for software — the hardware figure (205 ns) is parse-to-
decision inclusive. That only strengthens the FPGA comparison.

Run:
    python3 host/sw_latency_bench.py --n 2000 --seed 1 --repeats 5
    python3 host/sw_latency_bench.py --slice data/aapl_slice.bin
"""

import argparse, os, sys, time, statistics

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..', 'sim'))
sys.path.insert(0, HERE)

from golden.itch_parser import parse_stream
from golden.order_book_m2 import OrderBookM2
from golden.strategy_imbalance import StrategyImbalance
from phase2_golden import RiskCheck
import replay_gen

# Hardware reference (on-chip latency counter, 200 MHz)
HW_CYCLES = 41
HW_CLK_NS = 5
HW_LAT_NS = HW_CYCLES * HW_CLK_NS      # 205 ns
HW_JITTER_NS = 0                        # deterministic pipeline


def _one_pass(msgs, sink):
    """Run one full pass, appending each message's compute latency (ns) to sink."""
    book = OrderBookM2()
    strat = StrategyImbalance()
    risk = RiskCheck()
    pc = time.perf_counter_ns
    for m in msgs:
        t0 = pc()
        book.apply(m)
        s = book.snapshot()
        dec = strat.evaluate(s)
        if dec is not None:
            mid = (s.best_bid_price + s.best_ask_price) >> 1
            risk.check(dec.action, dec.price, dec.size, mid)
        t1 = pc()
        sink.append(t1 - t0)


def benchmark(stream, repeats):
    msgs = parse_stream(stream)
    # warm up caches / branch predictors (not measured)
    warm = []
    _one_pass(msgs, warm)
    lat = []
    for _ in range(repeats):
        _one_pass(msgs, lat)
    return msgs, lat


def pct(sorted_vals, p):
    if not sorted_vals:
        return 0
    k = min(len(sorted_vals) - 1, int(round((p / 100.0) * (len(sorted_vals) - 1))))
    return sorted_vals[k]


def main():
    ap = argparse.ArgumentParser(description="Software tick-to-trade latency benchmark (CPU baseline)")
    ap.add_argument('--n', type=int, default=2000)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--repeats', type=int, default=5, help="full passes over the stream")
    ap.add_argument('--slice', type=str, default=None, help="real carved ITCH slice")
    args = ap.parse_args()

    src = (f"slice:{args.slice}" if args.slice
           else f"synthetic n={args.n} seed={args.seed}")
    stream = (replay_gen.load_slice(args.slice) if args.slice
              else replay_gen.build_synthetic(args.n, args.seed))

    msgs, lat = benchmark(stream, args.repeats)
    lat.sort()
    n = len(lat)
    mn, mx = lat[0], lat[-1]
    med = statistics.median(lat)
    mean = statistics.fmean(lat)
    p90, p99 = pct(lat, 90), pct(lat, 99)
    jitter_std = statistics.pstdev(lat)
    jitter_span = mx - mn

    print("=" * 66)
    print(f"  Software tick-to-trade latency  (source: {src})")
    print(f"  messages={len(msgs)}  samples={n}  ({args.repeats} passes)  CPU compute only")
    print("=" * 66)
    print(f"  min     : {mn:>10,} ns")
    print(f"  median  : {med:>10,.0f} ns")
    print(f"  mean    : {mean:>10,.0f} ns")
    print(f"  p90     : {p90:>10,} ns")
    print(f"  p99     : {p99:>10,} ns")
    print(f"  max     : {mx:>10,} ns")
    print(f"  jitter  : stdev={jitter_std:,.0f} ns   span(max-min)={jitter_span:,} ns")
    print("-" * 66)
    print(f"  Hardware (FPGA, on-chip counter): {HW_LAT_NS} ns, jitter {HW_JITTER_NS} ns "
          f"({HW_CYCLES} cyc @ {1000//HW_CLK_NS} MHz)")
    print("-" * 66)
    print(f"  SPEEDUP  FPGA vs CPU  : {med / HW_LAT_NS:>8,.0f}x at median, "
          f"{p99 / HW_LAT_NS:,.0f}x at p99")
    print(f"  JITTER   FPGA vs CPU  : CPU spans {jitter_span:,} ns vs FPGA 0 ns "
          f"(deterministic)")
    print("=" * 66)
    print("  Note: SW compute only (parse excluded -> optimistic for SW). The FPGA")
    print("  205 ns is parse-to-decision inclusive. Broker network round-trip")
    print("  (~30 ms) is a separate, additional cost on the software path.")


if __name__ == '__main__':
    main()
