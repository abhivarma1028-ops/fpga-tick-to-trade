# axis_async_fifo — OUT-OF-CONTEXT ONLY constraints
# ===========================================================================
# Package this file with USED_IN = out_of_context  (NOT synthesis/implementation).
# See async_fifo_ooc.xdc for why these must not reach the integrated design.
# The constraints that survive integration live in axis_async_fifo.xdc.
#
# Wrapper around async_fifo; identical CDC story on the two AXI-Stream clocks.
#   s_clk : slave / producer clock
#   m_clk : master / consumer clock
create_clock -name s_clk -period 6.400 [get_ports s_clk]
create_clock -name m_clk -period 4.000 [get_ports m_clk]

set_clock_groups -asynchronous -group [get_clocks s_clk] -group [get_clocks m_clk]
