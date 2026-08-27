# =============================================================================
# drop_fixed_freq.tcl — make sync_2ff and handshake_mcp direction-agnostic.
#
#   cd ~/Projects/HFT
#   vivado -mode batch -source ip/drop_fixed_freq.tcl
#
# Both blocks are reusable in EITHER direction, but they were packaged with a
# fixed FREQ_HZ on each clock. IP Integrator treats a fixed FREQ_HZ as
# authoritative and refuses to reconcile it with the actual connected clock:
#
#   [BD 41-238] Port/Pin property FREQ_HZ does not match between
#               /u_risk_evt/src_clk(125000000) and /clk_core(250000000)
#
# In the block design handshake_mcp is used host->core (u_cfg) AND core->host
# (u_risk_evt), and sync_2ff sits on clk_core (u_halt) AND clk_host (the two
# overflow synchronizers). No single baked-in frequency can be right for both.
#
# async_fifo and axis_async_fifo already ship without FREQ_HZ for this exact
# reason -- the checker even calls it out as "correct if this block is used in
# both directions". This brings the other two into line.
#
# ASSOCIATED_RESET is kept: which reset belongs to which clock is a property of
# the block, not of how it is wired.
# =============================================================================

set IPROOT "/home/abhishek/IPs/IP for HFT"

foreach {b clocks} {sync_2ff      {clk}
                    handshake_mcp {src_clk dst_clk}} {
    set root "$IPROOT/$b"
    set comp "$root/component.xml"
    set xprs [glob -nocomplain [file join $root * *.xpr]]
    puts "\n================ $b ================"
    if {[llength $xprs] != 1} { puts "  expected one .xpr under $root"; continue }

    open_project -quiet [lindex $xprs 0]
    ipx::open_ipxact_file $comp
    set core [ipx::current_core]

    foreach c $clocks {
        set bif [ipx::get_bus_interfaces $c -quiet -of_objects $core]
        if {![llength $bif]} { puts "  WARNING: no '$c' interface"; continue }
        set p [ipx::get_bus_parameters FREQ_HZ -quiet -of_objects $bif]
        if {[llength $p]} {
            ipx::remove_bus_parameter FREQ_HZ $bif
            puts "  removed fixed FREQ_HZ from '$c'"
        } else {
            puts "  '$c' already has no FREQ_HZ"
        }
    }

    ipx::update_checksums $core
    ipx::save_core        $core
    ipx::unload_core      $comp
    close_project
}
puts "\n==== done ===="
