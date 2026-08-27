# axis_async_fifo — constraints that apply in EVERY context
# ===========================================================================
# Package this file with USED_IN = synthesis, implementation, out_of_context.
#
# Scoped to the IP instance, so `get_cells -hierarchical` searches inside this
# block only (it reaches the inner u_fifo). Nothing here names a clock, so it
# stays correct no matter what the integrating design calls its domains.

set_property ASYNC_REG TRUE [get_cells -hierarchical -filter {NAME =~ *rq_reg*}]
set_property ASYNC_REG TRUE [get_cells -hierarchical -filter {NAME =~ *wq_reg*}]

# Budget read from whichever clock is actually connected, so it tracks the real
# integrated frequency instead of the OOC placeholder.
set _s_c [get_clocks -quiet -of_objects [get_ports -quiet s_clk]]
set _m_c [get_clocks -quiet -of_objects [get_ports -quiet m_clk]]
set _s_p [expr {[llength $_s_c] ? [get_property PERIOD [lindex $_s_c 0]] : 6.400}]
set _m_p [expr {[llength $_m_c] ? [get_property PERIOD [lindex $_m_c 0]] : 4.000}]

# The -from list must include the BINARY pointer registers as well as the Gray
# ones. The Gray MSB equals the binary MSB, so synthesis drives q[MSB] straight
# from <p>bin_reg and no <p>gray_reg cell exists for that bit -- matching only
# *gray_reg* leaves the top pointer bit unconstrained.
# slave-domain Gray pointer captured in the master domain -> master period
set_max_delay -datapath_only \
    -from [get_cells -hierarchical -filter {NAME =~ *wgray_reg* || NAME =~ *wbin_reg*}] \
    -to   [get_cells -hierarchical -filter {NAME =~ *wq_reg*}] $_m_p
# master-domain Gray pointer captured in the slave domain -> slave period
set_max_delay -datapath_only \
    -from [get_cells -hierarchical -filter {NAME =~ *rgray_reg* || NAME =~ *rbin_reg*}] \
    -to   [get_cells -hierarchical -filter {NAME =~ *rq_reg*}] $_s_p
