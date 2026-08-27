# Prepare all five CDC IP projects for re-packaging.
# ===========================================================================
#   vivado -mode batch -source ip/prep_repackage_cdc_ips.tcl
#
# For each IP project this:
#   1. adds the new always-apply <block>.xdc to constrs_1 (idempotent)
#   2. sets USED_IN = out_of_context ONLY on <block>_ooc.xdc
#   3. sets USED_IN = synthesis/implementation/out_of_context + PROCESSING_ORDER
#      LATE on <block>.xdc
#   4. saves and closes the project
#
# It deliberately stops short of re-packaging: open each project in the GUI and
# use Package IP -> Merge changes from File Groups Wizard -> Re-Package IP.
# Set REPACKAGE to 1 below if you want this script to do that part too.
#
# The .sv and .xdc files inside each project were already refreshed from
# rtl/cdc/ on disk, so nothing needs copying first.

set REPACKAGE 0

set IPROOT "/home/abhishek/IPs/IP for HFT"
set BLOCKS {sync_2ff pulse_sync async_fifo axis_async_fifo handshake_mcp}

set ok 0
set bad {}

foreach b $BLOCKS {
    puts "\n================ $b ================"
    set root [file join $IPROOT $b]
    set xprs [glob -nocomplain [file join $root * *.xpr]]
    if {[llength $xprs] != 1} {
        puts "  SKIP: expected exactly one .xpr under $root, found [llength $xprs]"
        lappend bad $b ; continue
    }
    set xpr [lindex $xprs 0]

    if {[catch {
        puts "  opening [file tail $xpr]"
        open_project -quiet $xpr

        # The constraints dir is deterministic: <projdir>/<proj>.srcs/constrs_1/...
        set pdir   [file dirname $xpr]
        set xdcdir [file join $pdir "[file rootname [file tail $xpr]].srcs" \
                              constrs_1 imports xdc]
        set always [file join $xdcdir "${b}.xdc"]
        if {![file exists $always]} {
            error "$always is not on disk -- copy it from rtl/cdc/xdc/ first"
        }

        # 1. The OOC file may stay packaged, but ONLY tagged out_of_context.
        # It does create_clock on the block's own ports; untagged, those follow
        # the IP into the integrated design and shadow the real top-level clocks.
        # Tagging it out-of-context is what fixed that (verified: the shadow
        # clocks wr_clk/s_clk/src_clk disappeared from report_cdc).
        set oocf [get_files -quiet *${b}_ooc.xdc]
        if {[llength $oocf]} {
            set_property USED_IN {out_of_context} $oocf
            puts "  USED_IN ${b}_ooc.xdc = [get_property USED_IN $oocf]"
        } else {
            puts "  absent  ${b}_ooc.xdc (not in constrs_1 -- fine, Vivado generates OOC clocks)"
        }

        # 2. add the always-apply file if it is not already in the fileset.
        # NOTE: add_files/get_files read their file argument as a Tcl LIST, so a
        # path containing spaces ("IP for HFT") is silently split into several
        # bogus filenames -- [Vivado 12-172] File or Directory 'for' does not
        # exist. Wrap real paths in [list ...]; match by name pattern elsewhere.
        if {[llength [get_files -quiet *${b}.xdc]] == 0} {
            add_files -fileset constrs_1 -norecurse [list $always]
            puts "  added   ${b}.xdc"
        } else {
            puts "  present ${b}.xdc (already in constrs_1)"
        }

        # 3. Must apply in the INTEGRATED design, and after the cells it
        # references exist. Do NOT add out_of_context here: that tag is
        # exclusive, and it would restrict the file to the IP's own OOC run --
        # the ASYNC_REG and set_max_delay would then do nothing where they
        # matter, with no report anywhere saying so.
        set_property USED_IN {synthesis implementation} [get_files *${b}.xdc]
        set_property PROCESSING_ORDER LATE [get_files *${b}.xdc]

        update_compile_order -fileset sources_1

        puts "  USED_IN ${b}.xdc = [get_property USED_IN [get_files *${b}.xdc]]"

        if {$REPACKAGE} {
            set comp [file join $root component.xml]
            ipx::open_ipxact_file $comp
            ipx::merge_project_changes files [ipx::current_core]
            ipx::update_checksums [ipx::current_core]
            ipx::check_integrity  [ipx::current_core]
            ipx::save_core        [ipx::current_core]
            ipx::unload_core      $comp
            puts "  re-packaged -> $comp"
        }

        close_project
    } err]} {
        puts "  FAILED: $err"
        catch {close_project}
        lappend bad $b
        continue
    }
    incr ok
}

puts "\n================ summary ================"
puts "prepared OK : $ok / [llength $BLOCKS]"
if {[llength $bad]} { puts "problems in : [join $bad {, }]" }
if {!$REPACKAGE} {
    puts "\nNext: open each IP project in the GUI and do"
    puts "  Package IP -> File Groups -> Merge changes from File Groups Wizard"
    puts "  Package IP -> Review and Package -> Re-Package IP"
    puts "Then: python3 ~/Projects/HFT/ip/check_packaged_ip.py ~/IPs/IP\\ for\\ HFT/*"
}
