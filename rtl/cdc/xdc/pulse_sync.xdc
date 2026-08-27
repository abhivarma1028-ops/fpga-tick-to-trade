# pulse_sync — constraints that apply in EVERY context
# ===========================================================================
# Package this file with USED_IN = synthesis, implementation, out_of_context.
#
# Scoped to the IP instance. Nothing here names a clock, so it stays correct no
# matter what the integrating design calls its domains.

# Both synchronizer chains (forward toggle + returning ack).
set_property ASYNC_REG TRUE [get_cells -hierarchical -filter {NAME =~ *u_fwd*sync_reg*}]
set_property ASYNC_REG TRUE [get_cells -hierarchical -filter {NAME =~ *u_ack*sync_reg*}]

# Budget read from whichever clock is actually connected, so it tracks the real
# integrated frequency instead of the OOC placeholder.
set _src_c [get_clocks -quiet -of_objects [get_ports -quiet src_clk]]
set _dst_c [get_clocks -quiet -of_objects [get_ports -quiet dst_clk]]
set _src_p [expr {[llength $_src_c] ? [get_property PERIOD [lindex $_src_c 0]] : 8.000}]
set _dst_p [expr {[llength $_dst_c] ? [get_property PERIOD [lindex $_dst_c 0]] : 4.000}]

# forward toggle captured in the destination domain -> destination period
set_max_delay -datapath_only \
  -from [get_cells -hierarchical -filter {NAME =~ *src_tog_reg*}] \
  -to   [get_cells -hierarchical -filter {NAME =~ *u_fwd*sync_reg*}] $_dst_p
# dst_tog and tog_d_q are identical registers (same clock, reset and data), so
# synthesis merges them and no dst_tog_reg survives -- match whichever remains.
# Returning ack is captured back in the source domain -> source period.
set_max_delay -datapath_only \
  -from [get_cells -hierarchical -filter {NAME =~ *tog*_reg*}] \
  -to   [get_cells -hierarchical -filter {NAME =~ *u_ack*sync_reg*}] $_src_p
