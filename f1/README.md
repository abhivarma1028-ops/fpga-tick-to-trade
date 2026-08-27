# AWS F1 Bring-up (Phase 3)

Phase 3 proves the FPGA produces the same decisions as the golden software on
real-shaped data, and confirms the tick-to-trade latency on silicon. This dir
holds the host-side and build scaffolding. **Local prep is done; the AFI build
and on-hardware run require an AWS F1 environment.**

## Local vs AWS — what runs where

| Step | Where | Status |
|------|-------|--------|
| Cycle-level pipeline equivalence + latency in sim | local (Questa) | DONE — `sim/tb_phase3_pipeline.py` |
| Register map / CL-SH contract | local (doc) | DONE — `register_map.md` |
| Host replay/readback harness (offline self-check) | local | DONE — `host_replay.py --offline` |
| Wrap CL in the F1 Shell (`cl_tick_to_trade`) | AWS (HDK) | TODO — needs `aws-fpga` HDK |
| AFI build (`vivado` on a build instance) | AWS (z1d/c5 ~32 GB+) | TODO — too heavy for the 11 GB VM |
| Run on `f1.2xlarge`, DMA ITCH, read back | AWS (F1) | TODO |

## Measured so far (simulation)

- Isolated tick-to-trade latency: **41 cycles = 205 ns** @ 200 MHz
  (the +1 cycle vs the original 39 is the B3 2-stage strategy pipeline).
- Order-book equivalence vs golden: **0 mismatches** over 6 seeds
  (`sim/tb_replay_regression.py`).
- Strategy == golden == `host/strategy_sw.py`: 1,531 decisions, 0 mismatches.

## On-AWS build flow (when an F1 env is available)

1. `git clone https://github.com/aws/aws-fpga`; `source hdk_setup.sh`.
2. Create `cl_tick_to_trade` under `hdk/cl/developer_designs/` — instantiate
   `tick_to_trade_top` and wire the CL/SH interfaces per `register_map.md`:
   - `s_axis_*`  ← Shell AXI-Stream (ITCH from host DMA / DRAM).
   - `m_axis_*`  → Shell AXI-Stream (decisions back to host).
   - `s_axil_*`  ← Shell OCL/BAR0 AXI-Lite (latency histogram + clear).
   - `halt`, `risk_*` → sideband.
3. Drop in `rtl/top.xdc` constraints; the OOC false-paths are replaced by the
   Shell's real CL/SH timing — this is the in-context timing re-validation.
4. `aws_build_dcp_from_cl.sh` on a build instance → DCP → `create_fpga_image`
   → AFI id.
5. On `f1.2xlarge`: `fpga-load-local-image -S 0 -I <agfi>`; run
   `host_replay.py --device` to DMA an ITCH slice and read decisions + latency.

## Acceptance criteria (Phase 3 done)

- Hardware decision stream == golden over a real carved ITCH slice.
- In-context timing closes (no OOC artifacts; WNS ≥ 0 in the Shell build).
- Hardware-measured tick-to-trade latency ≈ 205 ns (matches simulation).
