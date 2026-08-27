# AWS F1 Register Map & CL/SH Interface

This is the contract the host (Phase 3 DMA test, later the live bridge) uses to
talk to the Custom Logic (CL) once it is wrapped in the F1 Shell. Everything
here is taken directly from the RTL (`rtl/latency_counter.sv`,
`rtl/tick_to_trade_top.sv`) and validated in simulation by
`sim/tb_phase3_pipeline.py`.

## AXI-Lite — latency / control (BAR0, slave `s_axil_*`)

`latency_counter.sv`, 32-bit registers, byte addresses:

| Address       | R/W | Name                  | Meaning                                            |
|---------------|-----|-----------------------|----------------------------------------------------|
| `0x000–0x0FC` | R   | `hist[0]…hist[63]`    | Tick-to-trade latency histogram, 64 buckets (4 B each). Bucket index = latency in clock cycles, capped at 63. |
| `0x100`       | R   | `last_latency_cycles` | Latency (cycles) of the most recent decision.      |
| `0x104`       | W   | `clear`               | Write `1` (bit 0) to zero the histogram + last_latency. |

Latency in ns = `cycles × 5.0` (200 MHz). Only bit 0 of write data is used;
`wstrb` is ignored (single 1-bit command register).

### CL bridge extension (`f1/cl_tick_to_trade_core.sv`)

The CL wraps the core behind a single OCL AXI-Lite. `0x000–0x1FF` pass straight
through to the latency counter above; the bridge adds register-based ITCH
ingress and decision egress so a host can drive the whole pipeline without a DMA
engine (Phase-3 bring-up — equivalence + latency, not throughput):

| Address       | R/W | Name             | Meaning                                              |
|---------------|-----|------------------|------------------------------------------------------|
| `0x200`       | W   | `itch_push`      | `wdata[7:0]` = ITCH byte, `wdata[8]` = tlast. Pushes the ingress FIFO → `s_axis`. |
| `0x204`       | R   | `status`         | bit0 ingress_full, bit1 ingress_empty, bit2 egress_overflow, `[15:8]` decision_count. |
| `0x2FC`       | R   | `decision_count` | Number of decisions captured.                        |
| `0x400+16*i`  | R   | `decision[i]`    | `+0x0` action (bit0), `+0x4` price, `+0x8` size.     |

Host flow: write `1`→`0x104` (clear) → push the framed ITCH stream byte-by-byte
to `0x200` (poll `0x204` bit0 for back-pressure) → read `0x2FC` then each
`decision[i]` → read the latency histogram. This is what `tb_cl_csr.py` exercises
in sim and what `f1/host_replay.py --device` will do on hardware.

**Measurement semantics (important):** `t0` is latched on `msg_start` (first byte
of a message) and `measuring` clears on `decision_valid`. With a *single tick in
flight* this yields the true tick-to-trade latency (~41 cycles ≈ 205 ns). Under
*continuous* streaming `t0` is reset by every message, so the histogram then
reflects the decision-to-most-recent-message gap, not causal latency. Measure
the headline latency in the isolated regime (see `tb_phase3_pipeline.py`).

## AXI-Stream — market data in (slave `s_axis_*`)

ITCH 5.0 byte stream, one byte per beat.

| Signal           | Width | Notes                                              |
|------------------|-------|----------------------------------------------------|
| `s_axis_tdata`   | 8     | One ITCH byte (framing-stripped payload).          |
| `s_axis_tvalid`  | 1     | Byte valid.                                         |
| `s_axis_tready`  | 1     | CL back-pressure — drops during book RESCAN. Honor it. |
| `s_axis_tlast`   | 1     | Last byte of the message.                           |

## AXI-Stream — decisions out (master `m_axis_*`)

| Signal           | Width | Notes                                              |
|------------------|-------|----------------------------------------------------|
| `m_axis_tdata`   | 72    | `{action[7:0], price[31:0], size[31:0]}` — action in bit 64, price 63:32, size 31:0. |
| `m_axis_tvalid`  | 1     | Decision valid (one beat per accepted order).      |
| `m_axis_tready`  | 1     | Host ready. No back-pressure expected (one beat/decision). |

## Sideband

| Signal         | Dir | Notes                                                       |
|----------------|-----|-------------------------------------------------------------|
| `halt`         | in  | Kill switch — 1 blocks all orders (risk R_HALT).            |
| `risk_reject`  | out | Pulse when a proposed order was blocked by risk.            |
| `risk_reason`  | out | 3-bit code: 0 OK, 1 SIZE, 2 PRICE, 3 POSITION, 4 HALT.      |

## Risk parameters (compile-time, `risk_check.sv`)

`MAX_ORDER_SIZE=500`, `MAX_POSITION=1000`, `MAX_PRICE_BAND=5000` ($0.50).
Reference price = book mid, pipelined 2 deep (`mid_r2`).

## Clocking / CL-SH note

OOC builds false-path the whole CL/SH boundary (see `rtl/top.xdc`, B5). On real
F1 the Shell owns and registers this boundary and the clock tree is built once
for the device, so those false-paths are replaced by the Shell's real timing —
which is what the Phase-3 AFI build re-validates in-context.
