# handshake_mcp — constraints that apply in EVERY context
# ===========================================================================
# Package this file with USED_IN = synthesis, implementation, out_of_context.
#
# Scoped to the IP instance. Nothing here names a clock, so it stays correct no
# matter what the integrating design calls its domains.
#
# Only the 1-bit req/ack toggles are synchronized (2FF each way). The WIDE
# data_hold -> dst_data bus is a MULTI-CYCLE PATH: the source holds it constant
# until the destination acknowledges, so it is stable for the whole round trip.
# It is relaxed with set_max_delay -datapath_only rather than forced to meet a
# single-cycle path it never needs to.

set_property ASYNC_REG TRUE [get_cells -hierarchical -filter {NAME =~ *u_req*sync_reg*}]
set_property ASYNC_REG TRUE [get_cells -hierarchical -filter {NAME =~ *u_ack*sync_reg*}]

# Budget read from whichever clock is actually connected, so it tracks the real
# integrated frequency instead of the OOC placeholder.
set _src_c [get_clocks -quiet -of_objects [get_ports -quiet src_clk]]
set _dst_c [get_clocks -quiet -of_objects [get_ports -quiet dst_clk]]
set _src_p [expr {[llength $_src_c] ? [get_property PERIOD [lindex $_src_c 0]] : 8.000}]
set _dst_p [expr {[llength $_dst_c] ? [get_property PERIOD [lindex $_dst_c 0]] : 4.000}]

# The req/ack toggles need bounding too, not just ASYNC_REG. Without these the
# only thing covering them is whatever the integrating design does, and if that
# is set_clock_groups -asynchronous the paths are not analyzed at all.
set_max_delay -datapath_only \
  -from [get_cells -hierarchical -filter {NAME =~ *req_tog_reg*}] \
  -to   [get_cells -hierarchical -filter {NAME =~ *u_req*sync_reg*}] $_dst_p
set_max_delay -datapath_only \
  -from [get_cells -hierarchical -filter {NAME =~ *ack_tog_reg*}] \
  -to   [get_cells -hierarchical -filter {NAME =~ *u_ack*sync_reg*}] $_src_p

# MCP data bus: held stable across the handshake -> one source period is the
# budget (deliberately the SOURCE period, not the destination's -- the hold
# guarantee comes from the source side).
set_max_delay -datapath_only \
  -from [get_cells -hierarchical -filter {NAME =~ *data_hold_reg*}] \
  -to   [get_cells -hierarchical -filter {NAME =~ *dst_data_reg*}] $_src_p
