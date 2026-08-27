# async_fifo — OUT-OF-CONTEXT ONLY constraints
# ===========================================================================
# Package this file with USED_IN = out_of_context  (NOT synthesis/implementation).
#
# Everything here describes the *standalone* world: it invents clocks on the
# block's own ports so the IP can be synthesized by itself. Once the IP is
# instantiated those ports are internal pins, and these create_clock calls
# become shadow clocks competing with the integrating design's real ones --
# which is exactly how `wr_clk`/`rd_clk` showed up in report_cdc instead of
# clk_line/clk_core.
#
# The constraints that must survive integration live in async_fifo.xdc.
#
# Representative OOC periods:
#   wr_clk : 156.25 MHz (6.4 ns) — RX-line domain
#   rd_clk : 250.00 MHz (4.0 ns) — core domain
create_clock -name wr_clk -period 6.400 [get_ports wr_clk]
create_clock -name rd_clk -period 4.000 [get_ports rd_clk]

# Standalone only. At integration the top level owns the async grouping between
# the real domain clocks (see cdc_top.xdc).
set_clock_groups -asynchronous -group [get_clocks wr_clk] -group [get_clocks rd_clk]
