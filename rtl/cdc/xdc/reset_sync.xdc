# reset_sync — constraints that apply in EVERY context
# ===========================================================================
# Package with USED_IN = synthesis, implementation (NO out_of_context tag --
# that tag is exclusive and would make this file inert exactly where it matters,
# with nothing in any report to tell you).
#
# Scoped to the IP instance. Names no clock and no port, so it stays valid at
# any depth in any hierarchy.

# Keep the release-synchronizer chain intact and packed together for settling.
# The RTL already marks it with (* ASYNC_REG = "TRUE" *); belt-and-suspenders.
set_property ASYNC_REG TRUE [get_cells -hierarchical -filter {NAME =~ *sync_reg*}]

# NOTE — the incoming `arst_n` cannot be constrained from in here: it arrives
# from outside the IP (a board pin or the shell reset tree), so its driver is
# not visible in this scope. The integrating design owns that, normally with
#     set_false_path -from [get_ports rst_*]
# which is what rtl/top_mc.xdc and rtl/xdc/mc_bd_top.xdc already do. Asserting
# the reset is genuinely asynchronous and needs no timing; the whole point of
# this block is that RELEASING it does not.
