# async_fifo — constraints that apply in EVERY context
# ===========================================================================
# Package this file with USED_IN = synthesis, implementation, out_of_context.
#
# Scoped to the IP instance, so `get_cells -hierarchical` searches inside this
# block only. Nothing here names a clock, so it stays correct no matter what
# the integrating design calls its domains.

# Synchronizer flops: packed together for maximum metastability settling, never
# merged or retimed away. The RTL already marks rq/wq with (* ASYNC_REG *);
# this is deliberate belt-and-suspenders.
set_property ASYNC_REG TRUE [get_cells -hierarchical -filter {NAME =~ *rq_reg*}]
set_property ASYNC_REG TRUE [get_cells -hierarchical -filter {NAME =~ *wq_reg*}]

# Bound each Gray-pointer crossing to one DESTINATION period, datapath only
# (ignore clock skew/uncertainty -- these are asynchronous paths). The budget is
# read from whichever clock is actually connected, so it tracks the real
# integrated frequency instead of the OOC placeholder above.
set _wr_c [get_clocks -quiet -of_objects [get_ports -quiet wr_clk]]
set _rd_c [get_clocks -quiet -of_objects [get_ports -quiet rd_clk]]
set _wr_p [expr {[llength $_wr_c] ? [get_property PERIOD [lindex $_wr_c 0]] : 6.400}]
set _rd_p [expr {[llength $_rd_c] ? [get_property PERIOD [lindex $_rd_c 0]] : 4.000}]

# The -from list must include the BINARY pointer registers as well as the Gray
# ones. The Gray MSB equals the binary MSB, so synthesis drives q[MSB] straight
# from <p>bin_reg and no <p>gray_reg cell exists for that bit -- matching only
# *gray_reg* leaves the top pointer bit unconstrained.
# write-domain Gray pointer captured in the read domain -> read period
set_max_delay -datapath_only \
    -from [get_cells -hierarchical -filter {NAME =~ *wgray_reg* || NAME =~ *wbin_reg*}] \
    -to   [get_cells -hierarchical -filter {NAME =~ *wq_reg*}] $_rd_p
# read-domain Gray pointer captured in the write domain -> write period
set_max_delay -datapath_only \
    -from [get_cells -hierarchical -filter {NAME =~ *rgray_reg* || NAME =~ *rbin_reg*}] \
    -to   [get_cells -hierarchical -filter {NAME =~ *rq_reg*}] $_wr_p
