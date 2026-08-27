# ─────────────────────────────────────────────────────────────────────────────
# top_mc.xdc — timing constraints for tick_to_trade_mc_top (3 clock domains)
#
# Target: AWS F-series shell (UltraScale+). As with top.xdc this is an
# OUT-OF-CONTEXT constraint set: the Shell owns the physical pins, so we
# constrain clocks and the CL/SH boundary budget only — never PACKAGE_PIN.
#
# XDC LANGUAGE NOTE: an .xdc accepts constraint commands plus basic set/expr
# only. proc / puts / if / foreach / remove_from_collection are rejected with
# [Designutils 20-1307] — and Vivado then SKIPS them and carries on, so the run
# appears to succeed with the constraint silently absent. Anything needing real
# Tcl must live in a .tcl script.
# ─────────────────────────────────────────────────────────────────────────────

# ── The three domains ────────────────────────────────────────────────────────
create_clock -name clk_line -period 6.400 [get_ports clk_line]   ;# 156.25 MHz  RX-line
create_clock -name clk_core -period 4.000 [get_ports clk_core]   ;# 250.00 MHz  core
create_clock -name clk_host -period 8.000 [get_ports clk_host]   ;# 125.00 MHz  host/CSR

set_clock_uncertainty 0.100 [get_clocks clk_line]
set_clock_uncertainty 0.100 [get_clocks clk_core]
set_clock_uncertainty 0.100 [get_clocks clk_host]

# ─────────────────────────────────────────────────────────────────────────────
# CDC CONSTRAINTS
#
# We deliberately do NOT write
#     set_clock_groups -asynchronous -group ... -group ... -group ...
#
# Clock groups remove the cross-domain paths from analysis ENTIRELY. That also
# overrides every set_max_delay, so the router gets no delay target and is free
# to skew a multi-bit crossing arbitrarily. For the Gray-coded FIFO pointers
# that dismantles the safety argument: "only one bit changes per increment"
# protects the destination only if the bits arrive within about one destination
# period of each other. With unbounded routing a slow bit from one increment can
# land after a fast bit from the next, and the destination captures a pointer
# value that never existed -> wrong full/empty -> lost or duplicated data.
# UG903 recommends set_max_delay -datapath_only over clock groups and false
# paths on CDC nets for exactly this reason.
#
# Bounding rather than ignoring has a second benefit: a crossing nobody
# constrained shows up as a timing violation instead of looking just as green as
# a correctly protected one.
#
# Budget: one clk_core period — the fastest destination of the three — applied
# uniformly so the bound is conservative for every crossing regardless of
# direction. Real routed delays here are a few hundred ps.
# ─────────────────────────────────────────────────────────────────────────────
set CDC_MAX 4.000

# ── Layer 1: clock-to-clock catch-all ────────────────────────────────────────
# Guarantees that NO cross-domain path is left unanalyzed, including any added
# later and not yet enumerated below.
set_max_delay -datapath_only -from [get_clocks clk_line] -to [get_clocks clk_core] $CDC_MAX
set_max_delay -datapath_only -from [get_clocks clk_core] -to [get_clocks clk_line] $CDC_MAX
set_max_delay -datapath_only -from [get_clocks clk_core] -to [get_clocks clk_host] $CDC_MAX
set_max_delay -datapath_only -from [get_clocks clk_host] -to [get_clocks clk_core] $CDC_MAX
set_max_delay -datapath_only -from [get_clocks clk_line] -to [get_clocks clk_host] $CDC_MAX
set_max_delay -datapath_only -from [get_clocks clk_host] -to [get_clocks clk_line] $CDC_MAX

# ── Layer 2: bus skew on the Gray-coded FIFO pointers ────────────────────────
# This is the constraint that actually encodes the Gray-code requirement.
# set_max_delay bounds each bit's ABSOLUTE delay, which bounds skew only
# indirectly; set_bus_skew bounds the RELATIVE arrival spread across the bus,
# which is precisely what "only one bit in flight at a time" needs. Xilinx's own
# XPM_CDC macros use both together.
#
# The -from list must include the BINARY pointer registers as well as the Gray
# ones: the Gray MSB equals the binary MSB, so synthesis drives q[MSB] straight
# from <p>bin_reg and no <p>gray_reg cell exists for that bit. Matching only
# *gray_reg* silently leaves the top pointer bit unconstrained — which is
# exactly the bit whose skew this constraint exists to bound.
#
# Unlike the cdc_integration block design, this top instantiates the CDC blocks
# as plain RTL, so every flop is visible here and these cell-level constraints
# resolve. In the packaged-IP flow the IPs are black boxes during top-level
# synthesis and the same filters return nothing ([Vivado 12-4739]).
set_bus_skew -from [get_cells -hierarchical -filter {NAME =~ *wgray_reg* || NAME =~ *wbin_reg*}] \
             -to   [get_cells -hierarchical -filter {NAME =~ *wq_reg*}] $CDC_MAX
set_bus_skew -from [get_cells -hierarchical -filter {NAME =~ *rgray_reg* || NAME =~ *rbin_reg*}] \
             -to   [get_cells -hierarchical -filter {NAME =~ *rq_reg*}] $CDC_MAX

# ── Layer 3: bus skew on the handshake_mcp payloads ──────────────────────────
# The MCP data bus crosses with no synchronizer by design — the source holds it
# constant until acknowledged, so it needs bounded skew, not synchronization.
# Vivado reports this structure as CDC-15, the correct signature of a working
# MCP (a genuinely unprotected bus reports CDC-1/CDC-2 critical instead).
set_bus_skew -from [get_cells -hierarchical -filter {NAME =~ *data_hold_reg*}] \
             -to   [get_cells -hierarchical -filter {NAME =~ *dst_data_reg*}] $CDC_MAX

# ─────────────────────────────────────────────────────────────────────────────
# CL/SH boundary
#
# Same rationale as top.xdc: in the real build the Shell owns and registers this
# interface, so these ports sit on Shell FFs rather than FPGA pads. The OOC run
# places them on PACKAGE_PIN stubs behind a global BUFG whose ~2.7 ns insertion
# delay does not cancel on I/O paths, producing violations on FF->OBUF paths
# with zero actual combinational delay. Constrain the boundary out so STA
# evaluates only real register-to-register paths.
#
# NOTE: remove_from_collection is not permitted in an .xdc, so the clocks are
# excluded with a get_ports filter instead.
# ─────────────────────────────────────────────────────────────────────────────
set_false_path -from [get_ports * -filter {DIRECTION == IN && NAME !~ clk_* && NAME !~ rst_*}]
set_false_path -to   [get_ports * -filter {DIRECTION == OUT}]

# Per-domain asynchronous resets. Asserted asynchronously, released
# synchronously to their own clock by the shell's reset tree.
set_false_path -from [get_ports rst_line_n]
set_false_path -from [get_ports rst_core_n]
set_false_path -from [get_ports rst_host_n]
