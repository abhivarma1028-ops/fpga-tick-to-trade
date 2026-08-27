# =============================================================================
# package_mc_glue.tcl — package the mc_glue IP from scratch.
#
#   cd ~/Projects/HFT
#   vivado -mode batch -source ip/package_mc_glue.tcl
#
# Packages FRESH each time (deletes any existing component.xml first). A merge
# into an existing package cannot undo a wrongly inferred bus interface, and the
# first attempt produced exactly that: Vivado saw ports named dec_tvalid /
# dec_tready and invented an AXI-Stream interface called 'dec', while inferring
# no clocks or resets at all. Those ports are now dec_valid / dec_accept.
#
# Bus interfaces are inferred EXPLICITLY here rather than relying on auto
# inference, and anything auto-inferred that we did not ask for is removed.
# =============================================================================

set XPR  "/home/abhishek/IPs/IP for HFT/mc_glue/mc_glue/mc_glue.xpr"
set ROOT "/home/abhishek/IPs/IP for HFT/mc_glue"
set COMP "$ROOT/component.xml"

proc set_bus_param {busif pname pvalue} {
    if {![llength [ipx::get_bus_parameters $pname -quiet -of_objects $busif]]} {
        ipx::add_bus_parameter $pname $busif
    }
    set_property value $pvalue [ipx::get_bus_parameters $pname -of_objects $busif]
}

open_project -quiet $XPR
set_property top mc_glue [current_fileset]
update_compile_order -fileset sources_1

# The OOC constraint file must be out-of-context ONLY: it does create_clock on
# the block's own clk_host/clk_core ports, which would otherwise become shadow
# clocks once the IP is instantiated. USED_IN_out_of_context is EXCLUSIVE.
set oocf [get_files -quiet *mc_glue_ooc.xdc]
if {[llength $oocf]} {
    set_property USED_IN {out_of_context} $oocf
    puts "  USED_IN mc_glue_ooc.xdc = [get_property USED_IN $oocf]"
} else {
    puts "  WARNING: mc_glue_ooc.xdc is not in constrs_1"
}

# ---- force a clean package --------------------------------------------------
if {[file exists $COMP]} {
    file delete -force $COMP
    puts "  removed stale component.xml (had a bogus 'dec' interface)"
}
file delete -force "$ROOT/xgui"

ipx::package_project -root_dir $ROOT -vendor hft -library hft \
                     -taxonomy /HFT/CDC -import_files -set_current true -force

set core [ipx::current_core]
set_property name         mc_glue $core
set_property display_name mc_glue $core
set_property description  "Non-CDC glue for the 3-domain tick-to-trade top: config re-send FSM, sticky overflow flags, risk-event gate, AXI-Lite field packing" $core

# ---- drop anything auto-inference invented ----------------------------------
# This block deliberately exposes loose wires; the only interfaces it should
# have are its two clocks and two resets.
set keep {clk_host clk_core rst_host_n rst_core_n}
foreach bif [ipx::get_bus_interfaces -of_objects $core] {
    set bname [get_property name $bif]
    if {[lsearch -exact $keep $bname] < 0} {
        puts "  removing auto-inferred interface '$bname'"
        ipx::remove_bus_interface $bname $core
    }
}

# ---- infer the clocks and resets explicitly ---------------------------------
foreach {pname vlnv} {clk_host  xilinx.com:signal:clock_rtl:1.0
                      clk_core  xilinx.com:signal:clock_rtl:1.0
                      rst_host_n xilinx.com:signal:reset_rtl:1.0
                      rst_core_n xilinx.com:signal:reset_rtl:1.0} {
    if {![llength [ipx::get_bus_interfaces $pname -quiet -of_objects $core]]} {
        if {[catch {ipx::infer_bus_interface $pname $vlnv $core} e]} {
            puts "  WARNING: could not infer '$pname': $e"
        } else {
            puts "  inferred interface '$pname'"
        }
    }
}

# ---- clock metadata ---------------------------------------------------------
foreach {ifname freq rstname} {clk_host 125000000 rst_host_n
                               clk_core 250000000 rst_core_n} {
    set bif [ipx::get_bus_interfaces $ifname -quiet -of_objects $core]
    if {![llength $bif]} { puts "  WARNING: no '$ifname' interface"; continue }
    set_bus_param $bif FREQ_HZ          $freq
    set_bus_param $bif ASSOCIATED_RESET $rstname
    puts "  $ifname : FREQ_HZ=[get_property value [ipx::get_bus_parameters FREQ_HZ -of_objects $bif]] ASSOCIATED_RESET=[get_property value [ipx::get_bus_parameters ASSOCIATED_RESET -of_objects $bif]]"
}

foreach rstname {rst_host_n rst_core_n} {
    set bif [ipx::get_bus_interfaces $rstname -quiet -of_objects $core]
    if {![llength $bif]} { puts "  WARNING: no '$rstname' interface"; continue }
    set_bus_param $bif POLARITY ACTIVE_LOW
    puts "  $rstname : POLARITY=[get_property value [ipx::get_bus_parameters POLARITY -of_objects $bif]]"
}

ipx::create_xgui_files $core
ipx::update_checksums  $core
ipx::check_integrity   $core
ipx::save_core         $core

puts "\n==== final interfaces ===="
foreach bif [ipx::get_bus_interfaces -of_objects $core] {
    puts "  [get_property name $bif]"
}
puts "==== packaged: $COMP ===="
close_project
