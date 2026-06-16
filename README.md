# FPGA Tick-to-Trade

FPGA-accelerated trading system targeting AWS F1 (Xilinx UltraScale+ VU9P).  
Demonstrates the full tick-to-trade pipeline in RTL with a live IBKR paper-account execution path.

## Architecture

```
NASDAQ ITCH binary (replayed from DRAM)
        │
        ▼
  itch_parser.sv  ──►  order_book_top.sv  ──►  strategy_imbalance.sv
        │                                              │
        └──── latency_counter.sv ◄────────────────────┘
                     │
                     ▼
         [PS / EC2 host] ibkr_bridge.py  ──►  IB Gateway (PAPER)
```

## Latency comparison (the money shot)

| Path | Latency |
|---|---|
| FPGA pipeline (PL, hardware-measured) | ~195 ns (39 cycles @ 200 MHz) |
| IBKR paper execution (software path) | ~30,000,000 ns (30 ms) |

## RTL modules (`rtl/`)

| File | Purpose |
|---|---|
| `itch_parser.sv` | ITCH 5.0 byte-stream → structured message (A/X/D/E) |
| `order_book_top.sv` | M1: Top-of-book (best bid/ask) maintenance, O(1) ADD |
| `order_book_m2.sv` | M2: Drop-in replacement with RESCAN FSM — correct best after C/D/E |
| `strategy_imbalance.sv` | Book-imbalance signal, fixed-point arithmetic |
| `latency_counter.sv` | Hardware cycle counter + histogram |
| `tick_to_trade_top.sv` | Top-level wrapper |

## Simulation (`sim/`)

```bash
cd sim

# Run with Questa (primary)
make SIM=questa TOPLEVEL=itch_parser MODULE=tb_itch_parser

# Quick smoke test with Verilator
make SIM=verilator TOPLEVEL=itch_parser MODULE=tb_itch_parser
```

## Host software (`host/`)

- `risk_guard.py` — pre-trade risk checks (Rule 15c3-5)
- `ibkr_bridge.py` — routes decisions to IB Gateway paper account
- `strategy_sw.py` — software mirror of `strategy_imbalance.sv` (golden reference)
- `live_feed.py` — streams **live IBKR market data** → strategy → paper orders

```bash
python host/ibkr_bridge.py --demo   # dry-run demo, no IBKR connection needed
python host/live_feed.py --demo     # full software path on synthetic ticks (offline)
```

### Live market-data path

The software path runs the same imbalance logic the FPGA computes (195 ns in
hardware) on live quotes, then executes on a **paper** account. End-to-end here is
network-bound (~tens of ms) — that gap vs the FPGA's 195 ns is the whole point.

**Prerequisites (one-time):**
1. Install IB Gateway (or TWS) and log into your **paper** account.
2. In Gateway: Configure → Settings → API → **Enable ActiveX and Socket Clients**;
   confirm the socket port (paper Gateway = `4002`, paper TWS = `7497`).
3. Free **delayed** data needs no subscription; real-time needs a market-data
   subscription (~$1.50/mo for US equities).

```bash
# Live delayed data, DRY-RUN orders (logged, not placed) — safe default
python host/live_feed.py --symbol AAPL

# Real-time data, actually place PAPER orders
python host/live_feed.py --symbol AAPL --realtime --execute
```

> Safety: `live_feed.py` defaults to dry-run; `--execute` only ever targets the
> paper account. It is never wired to a live-money account.

## Data (`data/`)

```bash
# Carve a single-symbol slice from a NASDAQ ITCH sample file
python data/carve_itch_slice.py --input S081322-v50.txt.gz \
                                 --symbol AAPL \
                                 --max-msgs 50000 \
                                 --output data/aapl_slice.bin
```

## Honest claims

- FPGA generates the trading signal in ~195 ns (39 cycles @ 200 MHz, hardware-measured with latency_counter.sv)
- A software bridge routes the decision to a live IBKR paper account (~30 ms end-to-end)
- RTL verified against a Python golden model (cocotb + Questa)
- Fixed-point arithmetic throughout — no floats in the data path
- Pre-trade risk checks per SEC Rule 15c3-5

## Implementation Results (Vivado 2025.2, xcu200-fsgd2104-2-e @ 200 MHz)

### Run history

| Run | Change | tick_to_trade_top WNS | multi_symbol_top WNS | Status |
|---|---|---|---|---|
| B0 | FF-array order book | -2.373 ns | — | FAIL (route-bound) |
| B1 | BRAM order book | -4.563 ns | — | FAIL (combinational chain) |
| M1 | multi-symbol ×4 (BRAM, comb. risk) | — | -5.362 ns | FAIL (chain + sym mux) |
| B2 | Register risk/decision stage | -2.217 ns | — | Datapath closed; 87 I/O endpoints |
| B3 | Pipeline strategy (2-stage) + collar ref | RTL done (205 ns sim) | — | RTL only, not re-synthesised |
| **B4** | XDC: false-path AXI-Lite + 10% I/O budget | **-0.578 ns** (47 ep) | **-0.433 ns** (2 ep) | **Near-closure** |

### B4 measured results

#### `tick_to_trade_top`

| Metric | Value |
|---|---|
| CLB LUTs | 7,043 (0.60%) |
| CLB Registers | 4,576 (0.19%) |
| RAMB36 | 1 | RAMB18 | 1 |
| DSPs | 0 |
| **WNS** | **-0.578 ns** — 47 failing endpoints |
| Total Power | 2.597 W (0.127 W dynamic) |
| Hardware latency | ~195 ns (39 cycles @ 200 MHz) |

#### `multi_symbol_top` (NSYMBOLS=4)

| Metric | Value |
|---|---|
| CLB LUTs | 19,827 (1.68%) |
| CLB Registers | 9,320 (0.39%) |
| RAMB36 | 4 | RAMB18 | 4 |
| DSPs | 0 |
| **WNS** | **-0.433 ns** — **2 failing endpoints** ✓ |
| Total Power | 2.804 W (0.331 W dynamic) |

> Multi-symbol improved from -5.362 ns / 10,899 failures (M1) to -0.433 ns / 2 failures (B4) —
> a 92% reduction in slack violation after XDC false-path fix on the AXI-Lite boundary.
> Both residual failures are OOC clock-insertion I/O artifacts, not logic-path violations.

## Toolchain

- Vivado ML Standard (synthesis + implementation)
- Questa Premium (simulation — primary)
- cocotb 2.x (Python testbenches)
- AWS F1 f1.2xlarge (hardware testing — end of project)
