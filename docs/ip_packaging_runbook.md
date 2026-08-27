# CDC IP Packaging & Block-Design Runbook

How to package the `rtl/cdc` library as out-of-context (OOC) Vivado IPs and stitch
them into the 3-domain tick-to-trade design in IP Integrator. **You drive Vivado
manually** — this is the step list, not an automated flow. There is a scripted
scaffold at `ip/package_cdc_ips.tcl` if you prefer batch packaging.

Prereq: verify the library first — `cd sim && make cdc-regression` must be green.

---

## 1. Package each block as an IP

For each block (`sync_2ff`, `pulse_sync`, `async_fifo`, `axis_async_fifo`,
`handshake_mcp`):

1. **File ▸ New Project** → RTL project, part `xcvu47p...` (the F2 shell part).
2. **Add Sources**: the block's `.sv` plus any it instantiates
   (`pulse_sync`+`sync_2ff`; `axis_async_fifo`+`async_fifo`;
   `handshake_mcp`+`sync_2ff`). Set the block as top.
3. **Add Constraints — package `<block>.xdc` ONLY. Never package `<block>_ooc.xdc`.**

   | File | Packaged? | Contains |
   |---|---|---|
   | `rtl/cdc/xdc/<block>.xdc` | **YES** — `USED_IN = synthesis, implementation, out_of_context` | `ASYNC_REG`, `set_max_delay -datapath_only` |
   | `rtl/cdc/xdc/<block>_ooc.xdc` | **NO** — keep on disk, out of `constrs_1` | `create_clock` on the block's own ports, `set_clock_groups` |

   ```tcl
   remove_files -fileset constrs_1 [get_files *<block>_ooc.xdc]
   add_files    -fileset constrs_1 -norecurse [list <path>/<block>.xdc]
   set_property USED_IN {synthesis implementation out_of_context} [get_files *<block>.xdc]
   set_property PROCESSING_ORDER LATE                             [get_files *<block>.xdc]
   ```

   **Why:** the `_ooc` file does `create_clock` on the block's own ports. Once the
   IP is instantiated those ports are internal pins, and the clocks become shadow
   copies competing with the real top-level ones — which is exactly what put
   `wr_clk`/`s_clk`/`src_clk` into `report_cdc` instead of
   `clk_line`/`clk_core`/`clk_host`, with `[Constraints 18-619] overwriting` and
   `[Constraints 18-513] no valid startpoints` alongside.

   **Vivado generates an IP's OOC and in-context clock constraints itself** from
   the clock interfaces and the actual block-design connections (the
   `*_in_context.xdc` files under `.gen/`). You do not supply them — no Xilinx IP
   in the 2025.2 catalog ships an `_ooc.xdc`. Setting `FREQ_HZ` correctly on each
   clock interface (step 5) is what feeds that generation.

   Trying to keep the `_ooc` file packaged-but-inert via `USED_IN` does **not**
   work: after `merge_project_changes` both files come back tagged identically
   (`USED_IN_out_of_context` in the synthesis fileset), so the distinction is not
   expressible. Remove it from the project instead.

   `<block>.xdc` names no clock and no port, so it is valid at any hierarchy depth
   and is what actually keeps the crossings constrained after integration.
   `PROCESSING_ORDER LATE` ensures the cells it references already exist.

   `ip/prep_repackage_cdc_ips.tcl` does all of the above for all five blocks.
   Verify after packaging with `python3 ip/check_packaged_ip.py <ip-dir>`.
4. **Tools ▸ Create and Package New IP ▸ Package your current project.**
5. In the Package IP wizard:
   - **Ports & Interfaces**: confirm clocks are recognized. For each clock port
     set **FREQ_HZ** and associate its reset (`ASSOCIATED_RESET`) and, for AXIS,
     its bus (`ASSOCIATED_BUSIF`). On `axis_async_fifo` the `s_axis_*` / `m_axis_*`
     ports should auto-infer as **AXI4-Stream** slave/master interfaces — if not,
     use *Auto Infer* or map them by name.
   - **Customization Parameters**: expose `WIDTH`/`DEPTH`/`STAGES` (FIFO),
     `TDATA_W`/`DEPTH` (AXIS), `WIDTH` (handshake), `STAGES` (synchronizers) so
     instances are configurable from the IP GUI.
   - **Review and Package.**
6. Set the IP's default **synthesis mode to Out-Of-Context (Global = OFF)** so each
   IP synthesizes independently and is cached — only changed IPs re-synth.

Repeat, or just run `ip/package_cdc_ips.tcl` which does all five in a loop.

---

## 2. Register the IP repository

`Settings ▸ IP ▸ Repository` → add `ip/cdc_repo` (or wherever you packaged), then
**Refresh / Update IP Catalog**. The CDC blocks now appear under *HFT/CDC*.

---

## 3. Assemble in IP Integrator (the 3-domain block design)

Create a block design and place the crossings at the domain boundaries:

- **RX-line → Core**: `axis_async_fifo` on the ITCH byte stream. `s_clk` = line
  clock, `m_clk` = core clock. Slave AXIS from the RX source; master AXIS into the
  parser.
- **Core → Host**: `axis_async_fifo` on the decision stream. `s_clk` = core,
  `m_clk` = host. Feeds the DMA/egress.
- **Host → Core config**: `handshake_mcp` for `strat_sel`/thresholds; `sync_2ff`
  for `halt`; `pulse_sync` for any host-issued strobe (`GO`, latency `clear`).
- **Core → Host telemetry**: `handshake_mcp` (or a small `axis_async_fifo`) to
  ship latency-counter snapshots back to the CSR domain.

Wire the three clocks from the **F1/F2 shell clock recipe** (`clk_main_a0` +
`clk_extra_*` groups): assign line/core/host to distinct groups at the frequencies
you set in each IP's FREQ_HZ. Connect matching AXIS interfaces by dragging; connect
clocks/resets per domain.

---

## 4. Verify the crossings in Vivado

After synthesis of the assembled design:

- **`report_cdc`** — every crossing must be recognized and **Safe**; zero Unsafe /
  Unknown. This is the acceptance gate.
- **`report_clock_interaction`** — cross-domain cells should show as async
  (no timed paths), consistent with the `set_clock_groups -asynchronous`.
- **`report_timing_summary`** — each clock meets its own period independently.

If `report_cdc` flags a crossing as Unknown, it usually means a signal bypassed the
CDC IPs (logic straddling the boundary) — fix by routing it through the right
primitive, not by adding a waiver.
