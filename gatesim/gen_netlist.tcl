open_checkpoint Vivado/HFT/HFT.srcs/utils_1/imports/synth_1/tick_to_trade_top.dcp
write_verilog -force -mode funcsim gatesim/tick_to_trade_top_funcsim.v
puts "=== PRIMITIVE LEAF CELLS ==="
set prims [dict create]
foreach c [get_cells -hier -filter {IS_PRIMITIVE}] {
    set r [get_property REF_NAME $c]
    dict incr prims $r
}
foreach k [lsort [dict keys $prims]] { puts "  $k : [dict get $prims $k]" }
close_design
