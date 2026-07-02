# cl_market_maker — hardware market-maker Custom Logic (AWS F2)

A second CL example that packages the **hardware market maker** for an F2 AFI,
mirroring `cl_tick_to_trade`. It hangs `market_maker_top` off the OCL AXI-Lite
control port.

## Contents (`design/`)
- `cl_market_maker.sv` — F2 Shell top (OCL → market_maker_top; all other Shell
  interfaces tied off, identical boilerplate to cl_tick_to_trade).
- `market_maker_top.sv` — AXI-Lite CSR accelerator.
- `strategy_market_maker.sv` — microprice + inventory-skew + spread quoting.
- `pipe_divu.sv` — 64-stage pipelined divider (lets it close 200 MHz).
- `latency_counter.sv` — GO→quote compute-latency histogram.
- `cl_market_maker_defines.vh`, `cl_id_defines.vh` — CL ID/defines.

## OCL register map
```
0x000-0x1FF : latency_counter histogram   0x100 R last_latency   0x104 W clear
0x200 W best_bid_price   0x214 W GO (strobe)     0x220 R status{bit0=quote_valid}
0x204 W best_ask_price   0x224 R bid_price       0x228 R bid_qty
0x208 W bid_size         0x22C R ask_price       0x230 R ask_qty
0x20C W ask_size
0x210 W inventory (signed 32b)
```
Host flow: write book+inventory → write GO → poll 0x220 → read quote 0x224-0x230;
read 0x100 for the measured GO→quote compute latency (66 cycles = 330 ns @200 MHz).

## Status (verified locally)
- `market_maker_top`: cocotb 40/40 CSR checks (sim/tb_market_maker_top.py).
- `strategy_market_maker`: cocotb 500/0 vs golden (sim/tb_strategy_market_maker.py).
- Vivado OOC synth @200 MHz xcu200: **WNS +1.004 ns, 0 failing eps**;
  9,237 LUTs / 9,045 regs / 8 DSP. Reports in `results/mm_synth/`.

## Build the AFI (when F-quota is available — same as cl_tick_to_trade)
On an EC2 FPGA Developer AMI (z1d) with the F2 HDK:
```
cd $HDK_DIR/cl/examples/cl_market_maker
export CL_DIR=$(pwd)
# point the build scripts at cl_market_maker (copy build/ from cl_tick_to_trade
# and set CL_NAME=cl_market_maker), then:
$HDK_DIR/cl/examples/cl_tick_to_trade/build/scripts/aws_build_dcp_from_cl.py \
    -c cl_market_maker -f BuildAll --no-encrypt -t mm_build
# then create-fpga-image from build/checkpoints/to_aws/*.Developer_CL.tar
```
NOTE: not yet built into an AFI — the F-instance quota (L-74FC7D96) is the same
blocker as cl_tick_to_trade. The RTL is synthesis- and timing-clean, so the CL is
AFI-ready the moment the quota clears.
