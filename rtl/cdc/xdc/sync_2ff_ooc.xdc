# sync_2ff — OUT-OF-CONTEXT ONLY constraints
# ===========================================================================
# Package this file with USED_IN = out_of_context  (NOT synthesis/implementation).
# See async_fifo_ooc.xdc for why these must not reach the integrated design.
# The constraints that survive integration live in sync_2ff.xdc.
#
# One destination clock; the input `d` is asynchronous (no launching clock).
create_clock -name clk -period 4.000 [get_ports clk]

# Standalone only. `d` is a real primary input here, so it is a valid timing
# startpoint. Once instantiated `d` is an internal pin driven by source-domain
# logic -- not a startpoint at all, which is what raised
#   [Constraints 18-513] ... contains no valid startpoints
# when the integrated design was opened. At integration the crossing into this
# block is covered by the top-level set_clock_groups -asynchronous, plus a
# set_max_delay -datapath_only from the driving flop (see sync_2ff.xdc).
set_false_path -from [get_ports d]
