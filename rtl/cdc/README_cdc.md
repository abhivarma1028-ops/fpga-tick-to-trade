# CDC IP Library

Hand-written, reusable clock-domain-crossing primitives for the tick-to-trade
pipeline's move to a 3-domain architecture (RX-line / Core / Host-CSR). Every
block is self-contained, parameterized, and packaged as an out-of-context (OOC)
Vivado IP with its own timing constraints (`xdc/*_ooc.xdc`).

**Golden rule enforced here:** arbitrary logic never straddles a clock boundary.
Only these dedicated primitives cross domains; everything else stays inside one
domain. Reviewers/interviewers can point at exactly where and how each crossing
happens.

## Blocks

| RTL | Purpose | Crossing type | Multi-clock TB |
|---|---|---|---|
| `sync_2ff.sv` | single-bit level into a new clock | 2-flop synchronizer | `sim/tb_sync_2ff.py` |
| `reset_sync.sv` | an async reset into a new clock | async assert, **sync release** | `sim/tb_reset_sync.py` |
| `pulse_sync.sv` | one-cycle strobe into a new clock | toggle + closed-loop ack | `sim/tb_pulse_sync.py` |
| `async_fifo.sv` | multi-bit data stream | Gray-pointer dual-clock FIFO | `sim/tb_async_fifo.py` |
| `axis_async_fifo.sv` | AXI-Stream data stream | async_fifo + AXIS wrapper | `sim/tb_axis_async_fifo.py` |
| `handshake_mcp.sv` | atomic multi-bit config word | req/ack MCP (data held stable) | `sim/tb_handshake_mcp.py` |

Run everything: `cd sim && make cdc-regression` (Verilator, both clock directions).

## When to use which

- **A reset entering a domain** → `reset_sync`, **one per clock domain**, and use
  its output as that domain's reset. Not optional: asserting a reset
  asynchronously is correct, but *releasing* it asynchronously means some flops
  see the release and some do not, so the design leaves reset in a state that was
  never designed for. Without them Vivado flags every reset endpoint as
  `[CDC-7] Asynchronous reset unknown CDC circuitry` — 4375 of those plus 441
  CDC-1 in the `cdc_brain_top` block design, ~91% of all its criticals.
- **One bit, level, capture-cycle doesn't matter** (e.g. `halt`) → `sync_2ff`.
- **One-cycle event must cross** (e.g. `GO`, `clear`) → `pulse_sync`. Never 2FF a
  raw pulse: it can be missed (src faster) or double-counted (src slower).
- **A stream of data words** (market-data beats, decision packets) → `async_fifo`
  / `axis_async_fifo`. Back-pressured, order/loss/dup-safe by construction.
- **A wide config value that changes rarely** (thresholds, `strat_sel`) →
  `handshake_mcp`. Never per-bit 2FF a bus — bits resolve on different cycles and
  the destination can momentarily see a value that never existed.

## Production swap: Xilinx XPM

These hand-written blocks are the primary (portfolio/interview) implementation.
For production, each maps 1:1 to a DO-qualified Xilinx XPM macro with vendor-proven
constraints — swap without changing the surrounding design:

| Hand-written | XPM equivalent |
|---|---|
| `sync_2ff` | `xpm_cdc_single` (or `xpm_cdc_array_single` for a bus of independent bits) |
| `reset_sync` | `xpm_cdc_async_rst` |
| `pulse_sync` | `xpm_cdc_pulse` |
| `async_fifo` | `xpm_fifo_async` |
| `axis_async_fifo` | `xpm_fifo_async` wrapper, or Xilinx **AXI4-Stream Data FIFO** / **AXI4-Stream Clock Converter** IP |
| `handshake_mcp` | `xpm_cdc_handshake` |

XPM pros: metastability-hardened library cells, built-in constraints, no XDC to
maintain. Cons: a black box you can't walk through gate by gate — which is exactly
why the hand-written versions exist here.

## Constraints

Two XDC files per block in `xdc/`, and only one of them is packaged:

| File | Packaged with the IP? | Contains | Why |
|---|---|---|---|
| `<block>.xdc` | **YES** — `USED_IN = synthesis, implementation, out_of_context` | `ASYNC_REG=TRUE` on synchronizer flops, `set_max_delay -datapath_only` on the crossings | Names no clock and no port, so it is valid at any depth in any hierarchy. This is what keeps the crossings constrained after integration. |
| `<block>_ooc.xdc` | **NO** — on disk only, for synthesizing the block standalone | `create_clock` per domain, `set_clock_groups -asynchronous`, `set_false_path` on ports | Describes the *standalone* world. Valid only while the block is the top of its own synthesis run. |

**If you package the `_ooc` file, it MUST be tagged `USED_IN = out_of_context`
(and nothing else).** It invents clocks on the block's own ports; untagged, those
become shadow clocks once the IP is instantiated, competing with the real
top-level ones. The tag is exclusive, not additive — which also means it must
never land on `<block>.xdc`, or that file goes inert exactly where it is needed
with nothing in any report to say so. That is precisely what once made `report_cdc` show
`wr_clk`/`s_clk`/`src_clk` instead of `clk_line`/`clk_core`/`clk_host`, with
`[Constraints 18-619] overwriting` on the way in and `[Constraints 18-513] no
valid startpoints` on the port-based false path.

Vivado generates each IP's OOC and in-context clock constraints itself, from the
clock interfaces and the actual block-design connections — no Xilinx IP in the
2025.2 catalog ships an `_ooc.xdc`. Set `FREQ_HZ` on every clock interface and let
the tool do it.

The `set_max_delay` budgets are read from whichever clock is actually connected
(`get_clocks -of_objects`), falling back to the OOC placeholder if none resolves —
so the bound tracks the real integrated frequency rather than a hardcoded guess.

Note that `sync_2ff` cannot constrain its own input crossing: the flop driving `d`
lives outside the IP. The integrating design owns that one, via top-level
`set_clock_groups -asynchronous` (see `cdc_top.xdc`) or a scoped `set_max_delay`.

Verify a packaged IP with `python3 ip/check_packaged_ip.py <ip-dir>`; it fails if
either file is missing or lands in the wrong fileset.
See `docs/ip_packaging_runbook.md` for the packaging steps.
