# =============================================================================
# package_reset_sync.tcl — create the project and package reset_sync as an IP
#
#   cd ~/Projects/HFT
#   vivado -mode batch -source ip/package_reset_sync.tcl
#
# Run with the Vivado GUI CLOSED: a GUI holding these IP projects overwrites
# packaged component.xml files with its own older in-memory view.
#
# Packages FRESH each time and infers the bus interfaces EXPLICITLY, because
# auto-inference has bitten us before (it invented a phantom AXI-Stream
# interface from ports merely named *_tvalid/*_tready).
#
# NO FIXED FREQ_HZ: reset_sync is instantiated on ALL THREE domains
# (156.25 / 250 / 125 MHz). A fixed FREQ_HZ is non-overridable in IP Integrator
# and would collide with the connected clock -- the exact [BD 41-238] failure
# that sync_2ff and handshake_mcp hit.
# =============================================================================

set ROOT "/home/abhishek/IPs/IP for HFT/reset_sync"
set PROJ "reset_sync"
set XPR  "$ROOT/$PROJ/$PROJ.xpr"
set COMP "$ROOT/component.xml"
set PART xcu200-fsgd2104-2-e
set HFT  "/home/abhishek/Projects/HFT"

file mkdir $ROOT
create_project -force $PROJ "$ROOT/$PROJ" -part $PART

add_files -norecurse                  [list $HFT/rtl/cdc/reset_sync.sv]
add_files -fileset constrs_1 -norecurse [list $HFT/rtl/cdc/xdc/reset_sync_ooc.xdc \
                                              $HFT/rtl/cdc/xdc/reset_sync.xdc]
set_property file_type SystemVerilog [get_files *reset_sync.sv]
set_property top reset_sync [current_fileset]
update_compile_order -fileset sources_1

# The two constraint flavours must not be interchanged.
# USED_IN_out_of_context is EXCLUSIVE: with it a file applies ONLY to this IP's
# own standalone run. That is right for the create_clock file and fatal for the
# always-apply one (its ASYNC_REG would go inert with nothing reporting it).
set_property USED_IN {out_of_context}          [get_files *reset_sync_ooc.xdc]
set_property USED_IN {synthesis implementation} [get_files *xdc/reset_sync.xdc]
set_property PROCESSING_ORDER LATE              [get_files *xdc/reset_sync.xdc]
puts "  USED_IN reset_sync_ooc.xdc = [get_property USED_IN [get_files *reset_sync_ooc.xdc]]"
puts "  USED_IN reset_sync.xdc     = [get_property USED_IN [get_files *xdc/reset_sync.xdc]]"

if {[file exists $COMP]} { file delete -force $COMP }
file delete -force "$ROOT/xgui"

ipx::package_project -root_dir $ROOT -vendor hft -library cdc \
                     -taxonomy /HFT/CDC -import_files -set_current true -force

set core [ipx::current_core]
set_property name         reset_sync $core
set_property display_name reset_sync $core
set_property description  "Reset synchronizer: asynchronous assert, synchronous release. One per clock domain." $core

# drop anything auto-inference invented
set keep {clk arst_n rst_n}
foreach bif [ipx::get_bus_interfaces -of_objects $core] {
    set bname [get_property name $bif]
    if {[lsearch -exact $keep $bname] < 0} {
        puts "  removing auto-inferred interface '$bname'"
        ipx::remove_bus_interface $bname $core
    }
}

foreach {pname vlnv} {clk    xilinx.com:signal:clock_rtl:1.0
                      arst_n xilinx.com:signal:reset_rtl:1.0
                      rst_n  xilinx.com:signal:reset_rtl:1.0} {
    if {![llength [ipx::get_bus_interfaces $pname -quiet -of_objects $core]]} {
        if {[catch {ipx::infer_bus_interface $pname $vlnv $core} e]} {
            puts "  WARNING: could not infer '$pname': $e"
        } else { puts "  inferred interface '$pname'" }
    }
}

# Both resets are active low. Deliberately NO FREQ_HZ on clk (see header).
foreach rname {arst_n rst_n} {
    set bif [ipx::get_bus_interfaces $rname -quiet -of_objects $core]
    if {![llength $bif]} { puts "  WARNING: no '$rname' interface"; continue }
    if {![llength [ipx::get_bus_parameters POLARITY -quiet -of_objects $bif]]} {
        ipx::add_bus_parameter POLARITY $bif
    }
    set_property value ACTIVE_LOW [ipx::get_bus_parameters POLARITY -of_objects $bif]
    puts "  $rname : POLARITY=[get_property value [ipx::get_bus_parameters POLARITY -of_objects $bif]]"
}

ipx::create_xgui_files $core
ipx::update_checksums  $core
ipx::check_integrity   $core
ipx::save_core         $core

puts "\n==== final interfaces ===="
foreach bif [ipx::get_bus_interfaces -of_objects $core] { puts "  [get_property name $bif]" }
puts "==== packaged: $COMP ===="
close_project
