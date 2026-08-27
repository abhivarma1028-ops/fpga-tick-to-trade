# pulse_sync — OUT-OF-CONTEXT ONLY constraints
# ===========================================================================
# Package this file with USED_IN = out_of_context  (NOT synthesis/implementation).
# See async_fifo_ooc.xdc for why these must not reach the integrated design.
# The constraints that survive integration live in pulse_sync.xdc.
#
# Toggle handshake across two asynchronous clocks; only 1-bit toggles cross.
create_clock -name src_clk -period 8.000 [get_ports src_clk]
create_clock -name dst_clk -period 4.000 [get_ports dst_clk]

set_clock_groups -asynchronous -group [get_clocks src_clk] -group [get_clocks dst_clk]
