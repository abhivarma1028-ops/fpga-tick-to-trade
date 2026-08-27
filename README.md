# FPGA Tick-to-Trade

A low-latency **tick-to-trade** trading pipeline in RTL — NASDAQ ITCH market data in,
an order decision out, in a hardware-measured **205 ns** — paired with a full software
trading stack (multi-symbol live feed, an HFT-style pre-trade decision engine, portfolio
risk, and two strategies) so the hardware and software paths can be compared directly.

Targets **AWS F2** (AMD/Xilinx Virtex UltraScale+ `xcvu47p`). The design is timing-closed,
the AFI is built, and a validated software model runs the same logic on live data.

---

## Status at a glance

| Piece | State |
|---|---|
| RTL tick-to-trade pipeline | ✅ complete, **timing closed** @ 200 MHz (WNS **+0.105 ns**, 0 failing endpoints) |
| Latency (hardware-measured in sim) | **205 ns** = 41 cycles @ 200 MHz |
| Verification (RTL vs golden model) | ✅ book + strategy equivalence, 0 mismatches |
| AWS F2 build | ✅ DCP built on `xcvu47p` (WNS +0.066 ns), **AFI `afi-0a02e7745a17daa6a` available** |
| Silicon run on `f2.6xlarge` | ⏳ gated on an AWS F-instance quota increase (support case pending) |
| Software trading stack | ✅ multi-symbol live feed, pre-trade gauntlet, portfolio, taker + maker strategies |

### An honest framing (read this first)
The FPGA and the live trades are linked by **equivalence, not execution**. Live/paper
trades run the **software model** (`strategy_sw.py`), which is proven bit-for-bit identical
to the RTL (see [Validation](#validation)). The 205 ns figure is the hardware pipeline
latency measured in simulation; Vivado then proved it is real by closing timing, area, and
power on the actual F2 device. The remaining step — loading the built AFI on an `f2.6xlarge`
and running it on silicon — is blocked only by an AWS quota, not by the design.

---

## Architecture — two paths, one logic

```
                      NASDAQ ITCH / live quotes
                               │
        ┌──────────────────────┴───────────────────────┐
        ▼ (hardware path — RTL)                         ▼ (software path — Python)
  itch_parser.sv                                  strategy_sw.py  (bit-identical
        │                                          to the RTL signal)
  order_book_m2.sv                                       │
        │                                          hft_logic.py   (pre-trade gauntlet)
  strategy_imbalance.sv                                  │
        │                                          portfolio.py   (PnL, exposure, kill switch)
  risk_check.sv                                          │
        │                                          ibkr_bridge / alpaca  ──► PAPER account
  latency_counter.sv  ── 205 ns ──►  (order)
```

Same decision logic on both sides. The hardware path is ~205 ns; the software path is
microseconds of compute plus tens of milliseconds of network — **that gap is the point.**

---

## The trade "brain" (software decision stack)

A raw signal is *not* a trade. Between the signal and an order sits a stack of checks
modelled on how real HFT desks decide — see **`docs/07_trade_logic_brain.pdf`** for a
plain-language explanation.

- **Signal core** (`strategy_sw.py`) — depth-weighted book-imbalance signal, identical to the RTL.
- **Pre-trade gauntlet** (`hft_logic.py`) — each signal must pass, in order:
  fair value (microprice) → edge vs cost → volatility / liquidity / staleness →
  inventory skew → portfolio exposure + **kill switch**. Every veto is counted (a *funnel*).
- **Portfolio** (`portfolio.py`) — cross-symbol positions, realized/unrealized PnL,
  net/gross exposure, daily-loss/drawdown kill switch.
- **Strategies**:
  - *Taker* (`strategy_sw.py`) — crosses the spread. Honest result: ~break-even after fees.
  - *Maker* (`market_maker.py`) — posts two-sided quotes, earns the spread, skews on inventory.

**Strategy comparison** (`backtest.py`, synthetic sweep, 1 bps fee):

| Strategy | Mean net PnL | Max drawdown | Inventory |
|---|--:|--:|--:|
| Taker (crosses spread) | −$13,139 | ~$10–18k | up to 10,300 |
| Maker (earns spread) | **+$620** | ~$300–450 | 0–800 (skew-bounded) |

The durable takeaway: crossing the spread bleeds; earning it (with inventory management)
pays. The project's real edge is **latency**, not the signal.

---

## Repository tour

| Path | What's there |
|---|---|
| `rtl/` | The hardware: `itch_parser`, `order_book_m2`, `strategy_imbalance`, `risk_check`, `latency_counter`, `tick_to_trade_top`, `multi_symbol_top` |
| `sim/` | cocotb testbenches + Python golden models (`golden/`) + `tb_strategy_sw_equiv.py`; `Makefile` |
| `host/` | Software stack: `strategy_sw`, `hft_logic`, `portfolio`, `market_maker`, `backtest`, `dashboard`, `live_feed`, `ibkr_bridge`, `risk_guard`, tests (`tb_*.py`) |
| `f1/`, `f2/` | AWS CL wrappers, host-replay driver, run scripts, and F2 build/quota runbooks |
| `docs/` | LaTeX + PDF write-ups (see [Documentation](#documentation)) |
| `results/` | Vivado synthesis/implementation reports (timing, area, power) |
| `reports/` | Live-run logs, `dashboard.png`, `run_summary.json` |
| `run_all_tests.sh` | One-command Python regression |

---

## Quickstart

```bash
# 1. Run the whole Python regression (equivalence + HFT logic + maker + smokes)
./run_all_tests.sh                       # or: cd sim && make regression

# 2. RTL simulation (needs Questa or Verilator)
cd sim && make SIM=questa TOPLEVEL=tick_to_trade_top MODULE=tb_phase3_pipeline

# 3. Prove the software model matches the RTL signal
cd sim && make strat-equiv               # 0 mismatches over 16k+ decisions

# 4. Live software prototype (dry-run, no gateway needed)
python host/live_feed.py --universe mag7 --simulate            # taker, all 7 mega-caps
python host/live_feed.py --universe mag7 --simulate --maker    # market-making mode

# 5. Backtest taker vs maker
python host/backtest.py                                  # synthetic sweep
python host/backtest.py --real-log reports/soak_aapl.log # on real AAPL quotes

# 6. Render the results dashboard  (reports/dashboard.png)
python host/dashboard.py

# 7a. LIVE web monitor (read-only) — feed + web server + opens the page
./run_live_monitor.sh                        # Ctrl-C stops both

# 7b. INTERACTIVE control panel — one server, controllable sim, open the page
python host/monitor_server.py                # http://localhost:8000/live.html
```

The interactive monitor lets you **start/stop/pause**, switch **taker↔maker**, change the
threshold, and **reset the kill switch** from the page, and adds a live latency histogram +
percentiles, an "FPGA time-saved" counter, an alerts feed, per-symbol drill-down, dark mode,
and flash-on-trade.

The live monitor (`reports/live.html`) auto-refreshes and shows P&L, per-symbol
positions, recent trades, the pre-trade funnel, exposure-vs-cap, SW-vs-FPGA latency,
kill-switch state, and a live log tail — fed by `reports/run_state.json`, which the
running feed rewrites every second.

Live trading against a **paper** account (IBKR Gateway or free Alpaca IEX) is documented in
`host/live_feed.py`; it defaults to dry-run and only ever targets a paper account with `--execute`.

---

## Results

### Implementation — `tick_to_trade_top` (timing CLOSED, xcu200 proto @ 200 MHz)
| Metric | Value |
|---|---|
| **WNS** | **+0.105 ns** — 0 failing endpoints (hold also met, WHS +0.008 ns) |
| CLB LUTs | 7,046 (0.60%) |
| CLB Registers | 4,573 (0.19%) |
| DSPs | 0 |
| Hardware latency | **205 ns** (41 cycles @ 200 MHz) |

Multi-symbol (`multi_symbol_top`, ×4) also closes. On the **F2 target (`xcvu47p`)** the DCP
built on an EC2 FPGA Developer AMI closed at **WNS +0.066 ns**, and the AFI baked to
`available` — full history in `docs/08_implementation_results.pdf` and `docs/12_aws_f2_cloud_build.pdf`.

### Latency — software vs hardware (real live-Alpaca soak, n=1,335)
| Path | Latency |
|---|---|
| FPGA pipeline (sim-measured) | **205 ns** (fixed, 0 jitter) |
| Software compute (this host) | ~7.2 µs median, ~40 µs p99 → **~35× slower** |
| + live order network round-trip | ~tens of ms (the separate, dominant real-world cost) |

---

## Validation

Hardware is linked to the live path by an **A ≡ B ≡ C** equivalence chain, not by running
the FPGA in the loop:

- **Book equivalence** — RTL `order_book_m2` == Python golden, 0 mismatches over realistic streams.
- **Strategy equivalence** — `strategy_sw.py` == golden == RTL, **0 mismatches over 16,361 decisions**
  (`sim/tb_strategy_sw_equiv.py`, run via `make strat-equiv`), locked in as a regression test.
- **Shadow compare** — same quote stream through RTL and software: 80/80 quotes, 44/44 signals match.
- **Gate-level sim** — post-route SDF back-annotation on real device delays.

So the 205 ns hardware number applies to the *same logic* the live path executes.

---

## AWS F2 status

The full FPGA build flow is done: synthesis + place-and-route on `xcvu47p` via the AWS HDK,
timing closed, DCP uploaded, and **AFI `afi-0a02e7745a17daa6a` / `agfi-006b5fd42e5f1cb6e`**
baked to `available`. Turnkey run scripts are in `f2/` (`launch_f2_run.sh`, `run_on_f2.sh`).

The only remaining step — launching one `f2.6xlarge` (24 vCPU) to load the AFI on real
silicon — is blocked on an AWS **F-instance quota** increase (a support case is pending; see
`f2/QUOTA_REREQUEST.md` and `f2/aws_sales_request.md`). Everything else is ready to run the
moment the quota clears.

---

## Honest claims & limitations

- ✅ FPGA generates the decision in **205 ns** (41 cycles @ 200 MHz), measured in sim via `latency_counter.sv`.
- ✅ RTL is timing/area/power-closed in Vivado; AFI built on the real F2 device.
- ✅ Live/paper trades run the **software model**, proven equivalent to the RTL — no FPGA in the live loop (yet).
- ✅ Fixed-point integer arithmetic throughout the RTL data path — no floats.
- ✅ Pre-trade risk mirrors SEC Rule 15c3-5 style checks.
- ⚠️ The market maker's profitability rests on a **modeled** fill assumption (noise flow); on
  one-sided real data even it bleeds a little. The robust result is latency + determinism, not alpha.
- ⚠️ Real-market data used for backtests is one symbol (AAPL), sampled at signal moments.
- ⏳ No on-silicon run yet (AWS quota-gated).

---

## Documentation (`docs/`)

| PDF | Topic |
|---|---|
| `02_executive_summary.pdf` | One-page technical brief |
| `01_hft_project_portfolio.pdf` | Portfolio overview |
| `13_project_complete_flow.pdf` | End-to-end architecture (TikZ) |
| `07_trade_logic_brain.pdf` | The pre-trade decision brain, in plain language |
| `08_implementation_results.pdf` | Vivado timing/area/power, from the .rpt data |
| `12_aws_f2_cloud_build.pdf` | The AWS F2 HDK build flow |
| `09_validation_roadmap.pdf` | 4-phase validation plan |
| `11_hardware_in_the_loop.pdf` | Honest account of what runs where |
| `03_why_fpga_beats_cpu_parallelism.pdf` | Spatial/pipeline parallelism explainer |

---

## Toolchain

- **Vivado 2025.2** (synthesis + implementation; xcu200 proto, xcvu47p F2 target)
- **Questa** (primary simulation) · **Verilator** (smoke) · **cocotb** (Python testbenches)
- **AWS F2** (`f2.6xlarge`, `xcvu47p`) via the AWS FPGA HDK + FPGA Developer AMI
- **Python** stack: numpy-free pure-Python models, matplotlib + pandas for the dashboard, `ib_async` / `alpaca-py` for live data
