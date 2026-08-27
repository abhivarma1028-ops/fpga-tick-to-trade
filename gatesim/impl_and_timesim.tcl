# Post-implementation timing-sim artifact generation for tick_to_trade_top.
# Opens the synthesis checkpoint, implements (opt/place/route), confirms timing,
# then writes the timing-simulation netlist + SDF (real cell/route delays).

set proj  /home/abhishek/Projects/HFT
set synth $proj/Vivado/HFT/HFT.srcs/utils_1/imports/synth_1/tick_to_trade_top.dcp
set out   $proj/gatesim

puts "=== Opening synthesis checkpoint ==="
open_checkpoint $synth

puts "=== opt_design ==="
opt_design
puts "=== place_design ==="
place_design
puts "=== phys_opt_design ==="
phys_opt_design
puts "=== route_design ==="
route_design

# Save the routed (implemented) checkpoint for reuse.
write_checkpoint -force $out/tick_to_trade_top_routed.dcp

puts "=== TIMING SUMMARY (post-route) ==="
report_timing_summary -delay_type min_max -max_paths 1 -file $out/post_route_timing.rpt
# echo WNS to the log
set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
puts "POST_ROUTE_WNS = $wns ns"

puts "=== Writing timing netlist + SDF ==="
write_sdf     -force $out/tick_to_trade_top_time.sdf
write_verilog -force -mode timesim -sdf_anno true \
              -sdf_file $out/tick_to_trade_top_time.sdf \
              $out/tick_to_trade_top_timesim.v

puts "=== DONE: artifacts in gatesim/ ==="
close_design
