# =============================================================================
# fix_and_repackage.tcl — finish Part A (RTL_Brain) and Part B (2 CDC IPs)
#
#   cd ~/Projects/HFT
#   vivado -mode batch -source ip/fix_and_repackage.tcl
#
# RTL_Brain:
#   * tag rtl_brain_ooc.xdc USED_IN=out_of_context so its create_clock on the
#     block's own `clk` port cannot follow the IP into the block design and
#     shadow the real clk_core
#   * add FREQ_HZ=250000000 and ASSOCIATED_RESET=rst_n to the clock interface
#   * re-package
#
# handshake_mcp / pulse_sync:
#   * their always-apply <block>.xdc is already in the project and on disk, it
#     just never got merged into the IP -- do the merge and re-package
# =============================================================================

set ok 0
set bad {}

# --- helper: (re)package one IP project ---------------------------------------
proc repack {label xpr comp} {
    puts "\n================ $label ================"
    if {![file exists $xpr]}  { puts "  MISSING project: $xpr";   return 0 }
    if {![file exists $comp]} { puts "  MISSING component: $comp"; return 0 }
    open_project -quiet $xpr
    update_compile_order -fileset sources_1
    ipx::open_ipxact_file $comp
    ipx::merge_project_changes files [ipx::current_core]
    return 1
}

proc finish {comp} {
    ipx::update_checksums [ipx::current_core]
    ipx::save_core        [ipx::current_core]
    ipx::unload_core      $comp
    close_project
}

# =============================================================================
# Part A — RTL_Brain
# =============================================================================
set BRAIN_ROOT "/home/abhishek/IPs/IP for HFT/RTL_Brain"
set BRAIN_XPR  "$BRAIN_ROOT/RTL_Brain/RTL_Brain.xpr"
set BRAIN_COMP "$BRAIN_ROOT/component.xml"

if {[catch {
    if {[repack "RTL_Brain" $BRAIN_XPR $BRAIN_COMP]} {
        # 1. the OOC constraint file must be out-of-context ONLY.
        # USED_IN_out_of_context is EXCLUSIVE, not additive: with it the file
        # applies only to this IP's own standalone run, which is exactly what we
        # want for a create_clock that names the block's own port.
        set oocf [get_files -quiet *rtl_brain_ooc.xdc]
        if {[llength $oocf]} {
            set_property USED_IN {out_of_context} $oocf
            puts "  USED_IN rtl_brain_ooc.xdc = [get_property USED_IN $oocf]"
            # re-merge so the retagged file lands in the core
            ipx::merge_project_changes files [ipx::current_core]
        } else {
            puts "  WARNING: rtl_brain_ooc.xdc not found in constrs_1"
        }

        # 2. clock metadata the block designer needs
        set clkif [ipx::get_bus_interfaces clk -of_objects [ipx::current_core]]
        if {[llength $clkif]} {
            foreach {p v} {FREQ_HZ 250000000 ASSOCIATED_RESET rst_n} {
                if {![llength [ipx::get_bus_parameters $p -quiet -of_objects $clkif]]} {
                    ipx::add_bus_parameter $p $clkif
                }
                set_property value $v [ipx::get_bus_parameters $p -of_objects $clkif]
                puts "  clk $p = [get_property value [ipx::get_bus_parameters $p -of_objects $clkif]]"
            }
        } else {
            puts "  WARNING: no 'clk' bus interface found"
        }

        finish $BRAIN_COMP
        incr ok
    } else { lappend bad RTL_Brain }
} err]} {
    puts "  FAILED: $err"
    catch {ipx::unload_core $BRAIN_COMP}
    catch {close_project}
    lappend bad RTL_Brain
}

# =============================================================================
# Part B — the two CDC IPs whose always-apply XDC never got merged
# =============================================================================
set IPROOT "/home/abhishek/IPs/IP for HFT"

foreach b {handshake_mcp pulse_sync} {
    set root "$IPROOT/$b"
    set comp "$root/component.xml"
    set xprs [glob -nocomplain [file join $root * *.xpr]]
    if {[llength $xprs] != 1} {
        puts "\n================ $b ================"
        puts "  expected one .xpr under $root, found [llength $xprs]"
        lappend bad $b
        continue
    }
    if {[catch {
        if {[repack $b [lindex $xprs 0] $comp]} {
            # belt and braces: make sure the always-apply file is tagged for the
            # integrated design and NOT restricted to out_of_context
            set af [get_files -quiet *${b}.xdc]
            if {[llength $af]} {
                set_property USED_IN {synthesis implementation} $af
                set_property PROCESSING_ORDER LATE $af
                puts "  USED_IN ${b}.xdc = [get_property USED_IN $af]"
                ipx::merge_project_changes files [ipx::current_core]
            } else {
                puts "  WARNING: ${b}.xdc not in constrs_1"
            }
            finish $comp
            incr ok
        } else { lappend bad $b }
    } err]} {
        puts "  FAILED: $err"
        catch {ipx::unload_core $comp}
        catch {close_project}
        lappend bad $b
    }
}

puts "\n================ summary ================"
puts "re-packaged OK : $ok / 3"
if {[llength $bad]} { puts "problems in    : [join $bad {, }]" }
