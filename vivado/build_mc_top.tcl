# =============================================================================
# build_mc_top.tcl — synthesize + implement tick_to_trade_mc_top (3 domains)
#
#   cd ~/Projects/HFT
#   vivado -mode batch -source vivado/build_mc_top.tcl
#
# Run with the Vivado GUI CLOSED. This is a single synthesis run and a single
# implementation run, so it does not hit the parallel-jobs memory problem that
# the out-of-context IP flow did -- but the GUI still holds 2-5 GB that P&R
# would rather have.
#
# Unlike the cdc_integration block design, the CDC blocks here are plain RTL
# inside the top. Nothing is a black box, so the cell-level set_bus_skew and
# set_max_delay constraints in rtl/top_mc.xdc actually resolve.
# =============================================================================

set ROOT   [file normalize [file dirname [info script]]/..]
set PROJ   mc_top
set OUTDIR $ROOT/vivado/$PROJ

# Matches the existing HFT Vivado project. For the real F2 shell build switch to
# xcvu47p-fsvh2892-2L-e (see docs/ip_packaging_runbook.md) -- the RTL and the
# constraints are part-independent, only the device changes.
set PART   xcu200-fsgd2104-2-e

# Synthesize out-of-context: on the AWS shell the physical pins belong to the
# Shell and the Custom Logic connects internally over AXI, so no I/O buffers
# should be inserted and no PACKAGE_PIN/IOSTANDARD may be set. Same reasoning as
# rtl/top.xdc.
set OOC    1

# ---------------------------------------------------------------- project
file mkdir $OUTDIR
create_project -force $PROJ $OUTDIR -part $PART

add_files -norecurse [glob $ROOT/rtl/*.sv]
add_files -norecurse [glob $ROOT/rtl/cdc/*.sv]
add_files -fileset constrs_1 -norecurse $ROOT/rtl/top_mc.xdc

set_property top tick_to_trade_mc_top [current_fileset]
set_property file_type SystemVerilog [get_files *.sv]
update_compile_order -fileset sources_1

puts "==== top is [get_property top [current_fileset]] on $PART ===="

if {$OOC} {
    set_property -name {STEPS.SYNTH_DESIGN.ARGS.MORE OPTIONS} \
                 -value {-mode out_of_context} -objects [get_runs synth_1]
    puts "==== synthesizing out-of-context (no I/O buffers; Shell owns the pins) ===="
}

# ---------------------------------------------------------------- synthesis
launch_runs synth_1 -jobs 1
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    error "synth_1 did not complete -- see [get_property DIRECTORY [get_runs synth_1]]/runme.log"
}
puts "==== synth_1 : [get_property STATUS [get_runs synth_1]] ===="

set SD [get_property DIRECTORY [get_runs synth_1]]
open_run synth_1 -name synth_1
report_utilization       -file          $SD/post_synth_utilization.rpt
report_cdc               -details -file $SD/post_synth_cdc.rpt
report_clock_interaction -file          $SD/post_synth_clock_interaction.rpt
report_timing_summary    -file          $SD/post_synth_timing_summary.rpt
puts "==== post-synthesis reports in $SD ===="
close_design

# ---------------------------------------------------------------- implementation
launch_runs impl_1 -jobs 1
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    error "impl_1 did not complete -- see [get_property DIRECTORY [get_runs impl_1]]/runme.log"
}
puts "==== impl_1 : [get_property STATUS [get_runs impl_1]] ===="

set ID [get_property DIRECTORY [get_runs impl_1]]
open_run impl_1
report_utilization       -file          $ID/post_route_utilization.rpt
report_timing_summary    -file          $ID/post_route_timing_summary.rpt
report_cdc               -details -file $ID/post_route_cdc.rpt
report_clock_interaction -file          $ID/post_route_clock_interaction.rpt
# Meaningful now: rtl/top_mc.xdc declares set_bus_skew on the Gray pointer buses
# and the MCP payload, which is what this report checks.
report_bus_skew -warn_on_violation -file $ID/post_route_bus_skew.rpt
report_drc               -file          $ID/post_route_drc.rpt

# ---------------------------------------------------------------- summary
puts "\n================ SUMMARY ================"
puts "WNS  [get_property STATS.WNS [get_runs impl_1]]   (setup)"
puts "WHS  [get_property STATS.WHS [get_runs impl_1]]   (hold)"
puts "TNS  [get_property STATS.TNS [get_runs impl_1]]"
puts "THS  [get_property STATS.THS [get_runs impl_1]]"
puts "reports: $ID"
puts "========================================"
