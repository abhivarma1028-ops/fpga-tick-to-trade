# package_cdc_ips.tcl — package the CDC library as out-of-context Vivado IPs
# ===========================================================================
# Repeatable IP-XACT packaging for every rtl/cdc block. Run from the project root
# in Vivado's Tcl console (or `vivado -mode batch -source ip/package_cdc_ips.tcl`).
# Produces one packaged IP per block under ip/cdc_repo/<name>/, each carrying its
# OOC XDC. Add ip/cdc_repo to your IP repository paths, then drop the blocks into
# IP Integrator at the RX->Core and Core->Host crossings.
#
# You run Vivado yourself — this is the script to source, not something Claude runs.

set PART        xcvu47p-fsvh2892-2L-e-es1   ;# F2 shell part; override as needed
set ROOT        [file normalize [file join [file dirname [info script]] ..]]
set RTL         $ROOT/rtl/cdc
set XDC         $ROOT/rtl/cdc/xdc
set REPO        $ROOT/ip/cdc_repo

# block  ->  {list of source .sv files}   (top module = first entry's basename)
dict set BLOCKS sync_2ff        [list $RTL/sync_2ff.sv]
dict set BLOCKS pulse_sync      [list $RTL/pulse_sync.sv $RTL/sync_2ff.sv]
dict set BLOCKS async_fifo      [list $RTL/async_fifo.sv]
dict set BLOCKS axis_async_fifo [list $RTL/axis_async_fifo.sv $RTL/async_fifo.sv]
dict set BLOCKS handshake_mcp   [list $RTL/handshake_mcp.sv $RTL/sync_2ff.sv]

file mkdir $REPO

dict for {name srcs} $BLOCKS {
    puts "==== packaging $name ===="
    set tmp [file join $REPO $name]
    file mkdir $tmp

    create_project -in_memory -part $PART pkg_$name
    add_files -norecurse $srcs
    # Two constraint files per block, and the USED_IN split between them is the
    # whole point. The _ooc file invents clocks on the block's own ports so it can
    # be synthesized standalone; if it also reached the integrated design those
    # would become shadow clocks competing with the real top-level ones (that is
    # what put wr_clk/s_clk/src_clk into report_cdc instead of clk_line/clk_core/
    # clk_host). The plain .xdc names no clock and no port, so it is valid in any
    # hierarchy and is what keeps the crossings constrained after integration.
    set ooc $XDC/${name}_ooc.xdc
    if {[file exists $ooc]} {
        add_files -fileset constrs_1 -norecurse $ooc
        set_property USED_IN {out_of_context} [get_files $ooc]
    }
    set always $XDC/${name}.xdc
    if {[file exists $always]} {
        add_files -fileset constrs_1 -norecurse $always
        set_property USED_IN {synthesis implementation out_of_context} \
            [get_files $always]
        # the cells it references must already exist when it is read
        set_property PROCESSING_ORDER LATE [get_files $always]
    }
    set_property top $name [current_fileset]
    update_compile_order -fileset sources_1

    # package into its own directory
    ipx::package_project -root_dir $tmp -vendor hft -library cdc \
        -taxonomy /HFT/CDC -import_files -set_current true -force

    # tag clock/reset inference; expose params in the IP GUI. AXIS interfaces on
    # axis_async_fifo are auto-inferred from the s_axis_*/m_axis_* port names.
    set core [ipx::current_core]
    set_property display_name  $name $core
    set_property description   "HFT CDC primitive: $name" $core

    ipx::create_xgui_files $core
    ipx::update_checksums   $core
    ipx::save_core          $core
    ipx::unload_core        $core
    close_project
    puts "     -> $tmp"
}

puts "All CDC IPs packaged under $REPO"
puts "Next: set_property ip_repo_paths $REPO \[current_project]; update_ip_catalog"
