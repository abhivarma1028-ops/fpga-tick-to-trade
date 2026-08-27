# rtl_brain — OUT-OF-CONTEXT ONLY constraints
# ===========================================================================
# Package this file with USED_IN = out_of_context  (NOT synthesis/implementation).
#
# This is the whole trading core (tick_to_trade_top) packaged as one IP: ITCH
# parser -> order book -> strategy -> risk check -> order emit, plus the latency
# histogram. It is entirely SINGLE-CLOCK. Every clock domain crossing lives
# OUTSIDE it, in the CDC blocks that surround it in the block design, so this IP
# needs no CDC exceptions of its own -- only a clock to be synthesized against
# when it is built out of context.
#
# WHY out_of_context ONLY: this create_clock names the block's own `clk` port.
# Once the IP is instantiated that port is an internal pin, and this clock
# becomes a shadow copy competing with the real top-level clk_core. That is
# exactly how wr_clk/s_clk/src_clk once ended up in report_cdc instead of the
# real domain names, with [Constraints 18-619] on the way in.
#
# NOTE: USED_IN_out_of_context is EXCLUSIVE, not additive. Do not add
# synthesis/implementation to it here, and do not let that tag land on any
# always-apply file -- it silently makes the constraints inert with nothing in
# any report to tell you.
#
# 250.00 MHz -- the core domain. This is the frequency the core is expected to
# close at; the single-clock build previously closed 200 MHz, and 250 MHz needed
# the latency_counter histogram pipelining (it was the only failing path).
create_clock -name clk -period 4.000 [get_ports clk]

# Jitter + on-chip variation budget, matching rtl/top.xdc.
set_clock_uncertainty 0.100 [get_clocks clk]
