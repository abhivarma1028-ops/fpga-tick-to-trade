# ─────────────────────────────────────────────────────────────────────────────
# mc_bd_top.xdc — TOP-LEVEL constraints for the block design that instantiates
#                 the RTL_Brain IP surrounded by the CDC IPs.
#
# Add this to the BD project's constrs_1 (NOT to any IP). It is the only place
# the three real domain clocks are defined.
#
# XDC LANGUAGE NOTE: an .xdc accepts constraint commands plus basic set/expr
# only. proc / puts / if / foreach / remove_from_collection are rejected with
# [Designutils 20-1307] -- and Vivado then SKIPS them and carries on, so the run
# appears to succeed with the constraint silently absent.
# ─────────────────────────────────────────────────────────────────────────────

create_clock -name clk_line -period 6.400 [get_ports clk_line]   ;# 156.25 MHz  RX-line
create_clock -name clk_core -period 4.000 [get_ports clk_core]   ;# 250.00 MHz  core / brain
create_clock -name clk_host -period 8.000 [get_ports clk_host]   ;# 125.00 MHz  host / CSR

set_clock_uncertainty 0.100 [get_clocks clk_line]
set_clock_uncertainty 0.100 [get_clocks clk_core]
set_clock_uncertainty 0.100 [get_clocks clk_host]

# ─────────────────────────────────────────────────────────────────────────────
# CDC bounds
#
# Deliberately NOT set_clock_groups -asynchronous. Clock groups remove the
# cross-domain paths from analysis ENTIRELY, which also overrides every
# set_max_delay and leaves the router with no delay target -- free to skew a
# multi-bit crossing arbitrarily. For the Gray-coded FIFO pointers that breaks
# the safety argument: "only one bit changes per increment" protects the
# destination only if the bits arrive within about one destination period of
# each other. UG903 recommends set_max_delay -datapath_only over clock groups
# and false paths on CDC nets for exactly this reason.
#
# Bounding also means a crossing nobody constrained shows up as a violation,
# instead of looking just as green as a correctly protected one.
#
# These are written CLOCK-to-CLOCK rather than cell-to-cell ON PURPOSE. In this
# block design both the RTL_Brain IP and the CDC IPs are packaged out-of-context,
# so while the top level is synthesized their internal cells do not exist and any
# get_cells filter naming them returns nothing:
#     [Vivado 12-4739] set_max_delay: No valid object(s) found for '-from ...'
# Clocks exist at every stage, so a clock-to-clock bound applies in synthesis and
# implementation alike and covers every crossing between the pair.
#
# TRADE-OFF, stated plainly: this is coarser than the cell-level set_bus_skew in
# rtl/top_mc.xdc, which measured the Gray pointer skew at ~1 ns against a 4 ns
# budget. Those cell-level constraints only resolve in the flat RTL build, where
# nothing is a black box. For per-bus skew bounds here, the CDC IPs must carry
# them internally -- i.e. ship <block>.xdc with USED_IN {synthesis
# implementation} and NO out_of_context tag (see rtl/cdc/xdc/).
#
# Budget: one clk_core period, the fastest destination, applied uniformly so the
# bound is conservative for every crossing regardless of direction.
# ─────────────────────────────────────────────────────────────────────────────
set CDC_MAX 4.000

set_max_delay -datapath_only -from [get_clocks clk_line] -to [get_clocks clk_core] $CDC_MAX
set_max_delay -datapath_only -from [get_clocks clk_core] -to [get_clocks clk_line] $CDC_MAX
set_max_delay -datapath_only -from [get_clocks clk_core] -to [get_clocks clk_host] $CDC_MAX
set_max_delay -datapath_only -from [get_clocks clk_host] -to [get_clocks clk_core] $CDC_MAX
set_max_delay -datapath_only -from [get_clocks clk_line] -to [get_clocks clk_host] $CDC_MAX
set_max_delay -datapath_only -from [get_clocks clk_host] -to [get_clocks clk_line] $CDC_MAX

# ─────────────────────────────────────────────────────────────────────────────
# CL/SH boundary — the Shell owns and registers this interface, so these ports
# sit on Shell flops rather than FPGA pads. Same rationale as rtl/top.xdc.
# remove_from_collection is not permitted in an .xdc, so the clocks and resets
# are excluded with a get_ports filter instead.
# ─────────────────────────────────────────────────────────────────────────────
set_false_path -from [get_ports * -filter {DIRECTION == IN && NAME !~ clk_* && NAME !~ rst_*}]
set_false_path -to   [get_ports * -filter {DIRECTION == OUT}]

set_false_path -from [get_ports rst_line_n]
set_false_path -from [get_ports rst_core_n]
set_false_path -from [get_ports rst_host_n]
