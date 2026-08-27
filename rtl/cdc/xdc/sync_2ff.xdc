# sync_2ff — constraints that apply in EVERY context
# ===========================================================================
# Package this file with USED_IN = synthesis, implementation, out_of_context.
#
# Scoped to the IP instance. Nothing here names a clock or a port, so it stays
# correct no matter what the integrating design calls its domains.

# Keep the synchronizer chain intact and packed for metastability settling.
# The RTL already marks it with (* ASYNC_REG = "TRUE" *); belt-and-suspenders.
set_property ASYNC_REG TRUE [get_cells -hierarchical -filter {NAME =~ *sync_reg*}]

# NOTE — the crossing INTO this block cannot be constrained from in here. The
# flop driving `d` lives in the source domain, outside the IP, so it is not
# visible in this scope. The integrating design must cover it, either with
#   set_clock_groups -asynchronous ...        (cdc_top.xdc already does this)
# or, for a tighter bound, at the top level:
#   set_max_delay -datapath_only -from [get_cells <driver>_reg] \
#                                -to   [get_cells <inst>/sync_reg[0]] <dst_period>
# The OOC-only companion file constrains it via set_false_path on the port,
# which is valid only while this block is the top of its own synthesis run.
