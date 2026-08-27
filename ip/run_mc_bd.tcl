# =============================================================================
# run_mc_bd.tcl — synthesize + implement the cdc_brain_top block design, headless
#
#   cd ~/Projects/HFT
#   vivado -mode batch -source ip/run_mc_bd.tcl
#
# Run with the Vivado GUI CLOSED. Two reasons:
#   * the GUI holds 2-5 GB that place & route would rather have
#   * a GUI holding these IP projects can overwrite packaged component.xml files
#
# Every run is launched with -jobs 1 and waited on, so the job count can never
# fan out. There are NINE out-of-context IP runs here (7 CDC/brain/glue IPs plus
# the axi_clock_converter and its sub-cores); at ~3.6 GB each, letting Vivado
# pick its own job count from 16 cores would need ~32 GB against 17 GB of RAM.
# That is what killed the earlier builds.
# =============================================================================

set PROJ_DIR "/home/abhishek/IPs/projects/mc_bd"
set XPR      "$PROJ_DIR/mc_bd.xpr"
set BD        cdc_brain_top

open_project $XPR

# ---- regenerate the block design output products and its per-IP runs --------
# Re-packaging or upgrading an IP drops these, and without them the top-level
# synthesis sees black boxes it cannot resolve.
set bdf [get_files -quiet *${BD}.bd]
if {[llength $bdf] != 1} { error "expected one ${BD}.bd, found [llength $bdf]" }
set bd [lindex $bdf 0]

puts "==== regenerating block design output products ===="
generate_target all [get_files $bd]
catch { export_ip_user_files -of_objects [get_files $bd] -no_script -sync -force -quiet }
catch { create_ip_run [get_files -of_objects [get_fileset sources_1] [get_files $bd]] }

# ---- discover the OOC runs rather than hardcoding their names ---------------
set ooc {}
foreach r [get_runs -quiet *] {
    if {[get_property IS_SYNTHESIS $r] && [get_property NAME $r] ne "synth_1"} {
        lappend ooc [get_property NAME $r]
    }
}
puts "==== [llength $ooc] out-of-context IP run(s) ===="
foreach r $ooc { puts "     $r" }

foreach r $ooc {
    puts "\n==== $r ===="
    if {[get_property STATUS [get_runs $r]] ne "Not started"} { reset_run $r }
    launch_runs $r -jobs 1
    wait_on_run $r
    set pr [get_property PROGRESS [get_runs $r]]
    puts "==== $r : [get_property STATUS [get_runs $r]] ($pr) ===="
    if {$pr ne "100%"} { error "$r did not complete -- stopping" }
}

# ---- top-level synthesis ----------------------------------------------------
puts "\n==== synth_1 ===="
if {[get_property STATUS [get_runs synth_1]] ne "Not started"} { reset_run synth_1 }
launch_runs synth_1 -jobs 1
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "synth_1 did not complete" }
puts "==== synth_1 : [get_property STATUS [get_runs synth_1]] ===="

set SD [get_property DIRECTORY [get_runs synth_1]]
open_run synth_1 -name synth_1
report_utilization       -file          $SD/post_synth_utilization.rpt
report_cdc               -details -file $SD/post_synth_cdc.rpt
report_clock_interaction -file          $SD/post_synth_clock_interaction.rpt
report_timing_summary    -file          $SD/post_synth_timing_summary.rpt
close_design

# ---- implementation ---------------------------------------------------------
puts "\n==== impl_1 ===="
if {[get_property STATUS [get_runs impl_1]] ne "Not started"} { reset_run impl_1 }
launch_runs impl_1 -jobs 1
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "impl_1 did not complete" }
puts "==== impl_1 : [get_property STATUS [get_runs impl_1]] ===="

set ID [get_property DIRECTORY [get_runs impl_1]]
open_run impl_1
report_utilization       -file          $ID/post_route_utilization.rpt
report_timing_summary    -file          $ID/post_route_timing_summary.rpt
report_cdc               -details -file $ID/post_route_cdc.rpt
report_clock_interaction -file          $ID/post_route_clock_interaction.rpt
report_drc               -file          $ID/post_route_drc.rpt

puts "\n================ SUMMARY ================"
puts "WNS  [get_property STATS.WNS [get_runs impl_1]]   (setup)"
puts "WHS  [get_property STATS.WHS [get_runs impl_1]]   (hold)"
puts "TNS  [get_property STATS.TNS [get_runs impl_1]]"
puts "THS  [get_property STATS.THS [get_runs impl_1]]"
puts "reports: $ID"
puts "========================================="
