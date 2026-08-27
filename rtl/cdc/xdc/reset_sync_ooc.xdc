# reset_sync — OUT-OF-CONTEXT ONLY constraints
# ===========================================================================
# Package with USED_IN = out_of_context  (NOT synthesis/implementation).
# create_clock names the block's own port; once instantiated that port is an
# internal pin and this would become a shadow clock competing with the real
# top-level one. USED_IN_out_of_context is EXCLUSIVE, not additive.
create_clock -name clk -period 4.000 [get_ports clk]

# `arst_n` is asynchronous by definition -- it has no launching clock, so there
# is no real path to time into the chain. Valid only while this block is the top
# of its own run; at integration the reset is constrained where it enters the
# design (see the set_false_path on rst_* in the top-level XDC).
set_false_path -from [get_ports arst_n]
