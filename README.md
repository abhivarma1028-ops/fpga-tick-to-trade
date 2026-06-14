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

## Toolchain

- Vivado ML Standard (synthesis + implementation)
- Questa Premium (simulation — primary)
- cocotb 2.x (Python testbenches)
- AWS F1 f1.2xlarge (hardware testing — end of project)
