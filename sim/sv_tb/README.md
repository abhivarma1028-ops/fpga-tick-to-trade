# Native SystemVerilog Testbenches (QuestaSim GUI + waveforms)

These are self-contained SystemVerilog testbenches for running the RTL directly
in the **QuestaSim GUI** so you can view waveforms and take screenshots. (The
`../tb_*.py` files are the cocotb/Python regression suite — same DUTs, different
front-end.)

Each `.do` script compiles the RTL + its testbench, loads the simulation, adds a
grouped Wave window, and runs to completion.

## One-time: put Questa on your PATH

```bash
export PATH=/home/abhishek/questasim2021/questasim/linux_x86_64:$PATH
```

## Run a testbench in the GUI

```bash
cd sim/sv_tb
vsim -do tt_top.do        # full pipeline  (ITCH -> decision, 195 ns)
vsim -do parser.do        # itch_parser
vsim -do book.do          # order_book_m2  (multi-level + RESCAN)
vsim -do strategy.do      # strategy_imbalance (depth-weighted + lots)
vsim -do latency.do       # latency_counter + AXI-Lite read
```

The Wave window opens pre-populated and the sim runs automatically. Then:

- **Wave > Zoom Full** (or type `wave zoom full` in the transcript) to fit the run.
- Drag to select a region and **Zoom In** for a close-up (e.g. the 39-cycle
  tick-to-trade span in `tt_top`).
- **File > Export > Image** (or a screenshot tool) to capture the waveform.

## Batch (no GUI, just check it runs)

```bash
vsim -c -do tt_top.do     # prints DECISION / LATENCY / RESULT to the transcript
```

## What each waveform shows

| Script | Highlights to screenshot |
|--------|--------------------------|
| `tt_top.do`   | `s_axis_*` byte stream in, parser `m_valid`, book best bid/ask, `decision_valid`, the 72-bit `m_axis_tdata`, and `last_latency_cycles` = 39 |
| `parser.do`   | `byte_cnt` ramping, `m_valid` pulse, decoded `order_ref/side/shares/price`; Replace shows `new_order_ref` |
| `book.do`     | `state` toggling IDLE/RESCAN, `best_bid_price` updating, `bid_level_*` depth buses |
| `strategy.do` | `w_bid`/`w_ask` weighted volumes, `cooldown_cnt`, `decision_valid`, scaled `order_size` |
| `latency.do`  | `free_cnt`/`t0`/`delta`, `measuring`, AXI-Lite `arvalid/rvalid` handshake, `rdata` |

> Tip: in `tt_top`, the latency span is from the first `s_axis_tvalid` of a
> message (t0) to `decision_valid` (t1). `last_latency_cycles` reads 39 — the
> 195 ns at 200 MHz.
