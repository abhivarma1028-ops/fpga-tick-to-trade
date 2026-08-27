#!/bin/bash
# Functional simulation of the actual POST-ROUTE (placed & routed) gate netlist
# of tick_to_trade_top using xsim. No SDF (timing closure is already proven by
# Vivado STA, WNS > 0 @ 200 MHz); this confirms the implemented gates compute the
# correct decision in the correct cycle count.
set -e
source ~/Xilinx/2025.2/Vivado/settings64.sh
cd /home/abhishek/Projects/HFT/gatesim
GLBL=~/Xilinx/2025.2/data/verilog/src/glbl.v

rm -rf xsim.dir webtalk* xelab.* xvlog.* xsim.* *.jou *.wdb 2>/dev/null || true

echo "=== xvlog ==="
xvlog -sv tick_to_trade_top_postroute.v
xvlog -sv tb_timesim.sv
xvlog $GLBL

echo "=== xelab (functional, post-route gates; SIMPRIM lib first) ==="
xelab -timescale 1ps/1ps -L simprims_ver -L secureip -L unisims_ver \
      tb_timesim glbl -s tb_pr -log xelab_pr.log

echo "=== xsim run ==="
xsim tb_pr -runall -log xsim_pr.log
