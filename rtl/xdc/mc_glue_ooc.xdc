# mc_glue — OUT-OF-CONTEXT ONLY constraints
# ===========================================================================
# Package this file with USED_IN = out_of_context  (NOT synthesis/implementation).
#
# mc_glue holds the non-CDC logic of tick_to_trade_mc_top: the config re-send
# FSM (host domain), the two sticky overflow flags (core domain), a config
# bit-slice, the risk-event launch gate and the AXI-Lite field packing.
#
# It has TWO clocks but NO crossing between them -- the host registers are driven
# only by host-domain signals and the core registers only by core-domain signals.
# Every real crossing lives in the CDC IPs that surround this block, which is
# where they are constrained. If a path between clk_host and clk_core ever
# appears inside this module, that is a bug: move it into a CDC block.
#
# So this file only gives the two clocks a definition for the block's own
# out-of-context synthesis run. No CDC exceptions belong here, and there is
# deliberately no always-apply companion file.
#
# WHY out_of_context ONLY: these create_clock calls name the block's own ports.
# Once the IP is instantiated those are internal pins, and the clocks would
# become shadow copies competing with the real top-level clk_host / clk_core --
# the same failure that once put wr_clk/s_clk/src_clk into report_cdc instead of
# the real domain names. USED_IN_out_of_context is EXCLUSIVE, not additive.
create_clock -name clk_host -period 8.000 [get_ports clk_host]   ;# 125.00 MHz
create_clock -name clk_core -period 4.000 [get_ports clk_core]   ;# 250.00 MHz

set_clock_uncertainty 0.100 [get_clocks clk_host]
set_clock_uncertainty 0.100 [get_clocks clk_core]
