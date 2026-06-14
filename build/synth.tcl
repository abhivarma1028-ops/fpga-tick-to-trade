# ─────────────────────────────────────────────────────────────────────────────
# synth.tcl — out-of-context synthesis + timing/utilization report
#
# Target: AWS F1 (Xilinx UltraScale+ VU9P)
#
# Usage (from the repo root or anywhere):
#   vivado -mode batch -source build/synth.tcl
#
# Produces, in build/:
#   tick_to_trade_top.dcp        synthesized checkpoint
#   timing_summary.rpt           full timing summary (check WNS >= 0)
#   utilization.rpt              LUT / FF / BRAM / DSP usage
#   synth.log                    Vivado run log (via -mode batch redirect)
#
# No board required — OOC synthesis targets the device part directly.
# ─────────────────────────────────────────────────────────────────────────────

# ── Paths (resolve relative to this script, so cwd does not matter) ──────────
set script_dir [file dirname [file normalize [info script]]]
set repo_root  [file dirname $script_dir]
set rtl_dir    [file join $repo_root rtl]
set out_dir    $script_dir

# ── Target device ─────────────────────────────────────────────────────────────
# xcu200 (Alveo U200) is the SAME VU9P UltraScale+ silicon as the AWS F1, in the
# same -2 speed grade — so local timing here is architecturally representative of
# F1. Swap to the commented F1 part when synthesizing on the AWS Developer AMI.
set part   "xcu200-fsgd2104-2-e"      ;# Alveo U200 (VU9P) — installed locally
# set part "xcvu9p-flgb2104-2-i"      ;# AWS F1 (needs licensed Vivado / AWS AMI)
set top    "tick_to_trade_top"

puts "INFO: synthesizing $top for part $part (out-of-context)"

# ── Read RTL sources (order independent for synth; deps resolved by Vivado) ──
set sources [list \
    [file join $rtl_dir itch_parser.sv]       \
    [file join $rtl_dir order_book_top.sv]    \
    [file join $rtl_dir order_book_m2.sv]     \
    [file join $rtl_dir strategy_imbalance.sv]\
    [file join $rtl_dir latency_counter.sv]   \
    [file join $rtl_dir tick_to_trade_top.sv] \
]
foreach f $sources {
    if {![file exists $f]} { error "missing source: $f" }
    read_verilog -sv $f
}

# ── Read constraints ─────────────────────────────────────────────────────────
read_xdc [file join $rtl_dir top.xdc]

# ── Synthesize, out-of-context (I/O owned by the F1 Shell) ───────────────────
synth_design -top $top -part $part -mode out_of_context -flatten_hierarchy rebuilt

# ── Checkpoint ───────────────────────────────────────────────────────────────
write_checkpoint -force [file join $out_dir ${top}.dcp]

# ── Reports ──────────────────────────────────────────────────────────────────
report_timing_summary -file [file join $out_dir timing_summary.rpt] \
    -max_paths 10 -delay_type min_max
report_utilization    -file [file join $out_dir utilization.rpt]

# ── Print the headline numbers to the console ────────────────────────────────
set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
puts "═══════════════════════════════════════════════════════════════"
puts "  SYNTHESIS COMPLETE: $top @ 200 MHz (5.000 ns)"
puts "  Worst Negative Slack (setup WNS): $wns ns"
if {$wns >= 0} {
    puts "  TIMING MET ✓  (design closes at 200 MHz)"
} else {
    puts "  TIMING VIOLATED ✗  (see build/timing_summary.rpt)"
}
puts "  Reports: build/timing_summary.rpt  build/utilization.rpt"
puts "═══════════════════════════════════════════════════════════════"
