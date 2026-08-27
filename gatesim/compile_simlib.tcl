# Compile Xilinx simulation libraries for QuestaSim (needed for gate-level
# timing simulation of the post-implementation netlist). Scoped to the
# virtexuplus family (xcu200) and all libraries (unisim + simprim + secureip),
# since the timing netlist + SDF reference SIMPRIM/secureip timing cells.
compile_simlib -directory /home/abhishek/Projects/HFT/gatesim/simlib \
  -simulator questa \
  -simulator_exec_path /home/abhishek/questasim_ns/linux_x86_64 \
  -family virtexuplus -language verilog \
  -library unisim -library simprim \
  -no_systemc_compile -force
