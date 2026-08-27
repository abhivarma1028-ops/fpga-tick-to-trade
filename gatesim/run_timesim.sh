#!/bin/bash
# Gate-level TIMING simulation of tick_to_trade_top in QuestaSim, with the
# post-route SDF back-annotated (real cell + routing delays). The netlist already
# embeds $sdf_annotate, so just compiling + running applies the delays.
set -e
export PATH=/home/abhishek/questasim_ns/linux_x86_64:$PATH
cd /home/abhishek/Projects/HFT/gatesim

SIMLIB=/home/abhishek/Projects/HFT/gatesim/simlib
GLBL=/home/abhishek/Xilinx/2025.2/data/verilog/src/glbl.v
INI=$SIMLIB/modelsim.ini

rm -rf work
vlib work
echo "=== vlog (netlist + glbl + tb) ==="
vlog -work work -timescale "1ps/1ps" -modelsimini $INI \
     -suppress 2892,2388 \
     $GLBL tick_to_trade_top_timesim.v tb_timesim.sv

echo "=== vsim (SDF back-annotated, real delays) ==="
vsim -c -modelsimini $INI \
     -L unisims_ver -L simprims_ver -L secureip -L unimacro_ver -L xpm \
     -voptargs="+acc" \
     work.tb_timesim work.glbl \
     -do "run -all; quit -f"
