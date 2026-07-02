# Alveo / Vitis Port Scope — running tick-to-trade off the AWS shell

Goal: run the *same* `tick_to_trade_top` on a free **AMD/Xilinx HACC** Alveo card
(U280 = `xcu280`/`xcvu37p`, or U55C) via **Vitis/XRT**, since the AWS AFI is
AWS-shell-specific and won't load elsewhere.

## Why this is tractable
`tick_to_trade_top` is **device-agnostic**: plain `clk/rst_n`, an 8-bit AXI4-Stream
in (`s_axis_*`), a 72-bit AXI4-Stream out (`m_axis_*`), `halt/risk_reject/risk_reason`
control, and a 9-bit AXI4-Lite slave (`s_axil_*`, latency-counter readout). It uses
only generic UltraScale+ primitives (RAM256X1D, RAMB18E2, FDRE…). **All AWS-specific
logic is in `cl_tick_to_trade.sv` (the Shell wrapper) — the core has none.** The
existing `cl_tick_to_trade_core.sv` bridge (OCL→register: push ITCH @0x200, read
decisions @0x400, status @0x204, histogram @0x000) is exactly the host model we want;
it just needs re-fronting with a Vitis-style AXI4-Lite control interface.

## Target & toolchain
- Card: Alveo U280 (`xilinx_u280_gen3x16_xdma_*` platform) — UltraScale+ HBM, same
  family as the F2 `xcvu47p`. RTL ports unchanged.
- Tools: Vitis `v++` (package + link), XRT runtime. Available on HACC nodes.
- Clock: design closes at 200 MHz; set kernel clock to 200–250 MHz in the v++ config.

## Recommended architecture: user-managed (XRT `xrt::ip`) RTL kernel
Reuse the register-bridge model (no DMA engine needed for bring-up):
1. Wrap `tick_to_trade_top` + the `cl_tick_to_trade_core` bridge as an **RTL kernel**
   exposing one **AXI4-Lite control (`s_axi_control`)** interface = the bridge's
   register map (push/status/decisions/histogram), plus `ap_clk`/`ap_rst_n`.
2. Mark it a **user-managed kernel** (`ctrlProtocol = user_managed` in `kernel.xml`)
   so XRT does MMIO reads/writes via `xrt::ip::write_register/read_register` — the
   direct analogue of the BAR0 mmap in `f1/host_replay.py --device`.
3. `package_xo` → `v++ --link` against the U280 platform → `.xclbin` (P&R on the card,
   ~hours).

## Host program
Port `f1/host_replay.py`'s device path to **pyxrt / XRT C++**:
- `device = xrt.device(0); xclbin = device.load_xclbin("tick_to_trade.xclbin")`
- `ip = xrt.ip(device, xclbin, "tick_to_trade_top")`
- replace `bar.poke/peek(off,val)` with `ip.write_register(off,val)` /
  `ip.read_register(off)` — **the register offsets are identical** (0x104 clear,
  0x200 push, 0x204 status, 0x2FC count, 0x400+16i decisions, 0x000-0x0FC hist).
- The whole drive/compare sequence (split_framed → push → read → diff vs
  phase2_golden) is reused verbatim.

## Work breakdown (estimate)
| Task | Effort | Notes |
|------|--------|-------|
| Package core+bridge as RTL kernel (kernel.xml, AXI4-Lite control, packaging tcl) | ~1–2 days | bridge logic already exists |
| `v++ --link` to U280 xclbin (build + timing) | hours (compute) | needs HACC node w/ Vitis |
| Host port to pyxrt (`xrt::ip` register access) | ~0.5 day | offsets unchanged from host_replay |
| Bring-up + diff vs golden on hardware | ~0.5 day | same equivalence check |

## Gating prerequisite
**Academic affiliation** for HACC access (register at the AMD HACC portal with a
university email). Without it, this path isn't available and the AWS sales route
(`f2/aws_sales_request.md`) is the way back to the original F2 target.

## Reuse summary
Unchanged: all RTL (`tick_to_trade_top` + submodules + bridge), the register map,
the golden model, and the host drive/diff logic. New: a `kernel.xml` + packaging
tcl, a v++ link config, and ~30 lines swapping mmap calls for `xrt::ip` calls.
