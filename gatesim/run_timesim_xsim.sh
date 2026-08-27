#!/bin/bash
# Gate-level TIMING simulation of tick_to_trade_top using Vivado's xsim
# (precompiled simulation libraries — no compile_simlib needed). The post-route
# SDF is back-annotated (embedded $sdf_annotate in the netlist => real delays).
set -e
source ~/Xilinx/2025.2/Vivado/settings64.sh
cd /home/abhishek/Projects/HFT/gatesim

GLBL=~/Xilinx/2025.2/data/verilog/src/glbl.v

rm -rf xsim.dir webtalk* xelab.* xvlog.* xsim.* *.jou *.wdb 2>/dev/null || true

echo "=== xvlog: compile netlist + tb + glbl ==="
xvlog -sv tick_to_trade_top_timesim.v
xvlog -sv tb_timesim.sv
xvlog $GLBL

echo "=== xelab: elaborate (implemented netlist => SIMPRIM lib FIRST; SDF embedded) ==="
# Library order matters: the implemented netlist uses SIMPRIM primitives
# (IBUFCTRL/INBUF/RAMD64E), so simprims_ver + secureip must resolve BEFORE
# unisims_ver, otherwise the cells bind to the functional unisim models which
# refuse to simulate implemented netlists.
xelab -timescale 1ps/1ps \
      -L simprims_ver -L secureip -L unisims_ver \
      tb_timesim glbl -s tb_snapshot -log xelab_run.log

echo "=== xsim: run ==="
xsim tb_snapshot -runall -log xsim_run.log
