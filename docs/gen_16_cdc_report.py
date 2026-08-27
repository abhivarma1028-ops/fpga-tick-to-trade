#!/usr/bin/env python3
"""Generate the CDC IP library + integration report PDF."""
import os
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph,
                                Spacer, Table, TableStyle, PageBreak, KeepTogether)

OUT = "/home/abhishek/Projects/HFT/docs/16_cdc_ip_library_and_integration.pdf"

INK    = colors.HexColor("#1a1a1a")
MUTED  = colors.HexColor("#5b6472")
ACCENT = colors.HexColor("#0b5394")
RULE   = colors.HexColor("#c9d2dd")
BG     = colors.HexColor("#eef3f8")
OKBG   = colors.HexColor("#e7f4ea")
WARNBG = colors.HexColor("#fdf3e3")

ss = getSampleStyleSheet()
S = {}
S['title'] = ParagraphStyle('title', parent=ss['Title'], fontName='Helvetica-Bold',
                            fontSize=23, leading=27, textColor=INK, spaceAfter=4)
S['sub'] = ParagraphStyle('sub', parent=ss['Normal'], fontName='Helvetica',
                          fontSize=11.5, leading=16, textColor=MUTED, spaceAfter=2)
S['h1'] = ParagraphStyle('h1', parent=ss['Heading1'], fontName='Helvetica-Bold',
                         fontSize=15, leading=19, textColor=ACCENT,
                         spaceBefore=16, spaceAfter=7)
S['h2'] = ParagraphStyle('h2', parent=ss['Heading2'], fontName='Helvetica-Bold',
                         fontSize=11.8, leading=15, textColor=INK,
                         spaceBefore=11, spaceAfter=4)
S['h3'] = ParagraphStyle('h3', parent=ss['Heading3'], fontName='Helvetica-BoldOblique',
                         fontSize=10.2, leading=13, textColor=MUTED,
                         spaceBefore=8, spaceAfter=3)
S['body'] = ParagraphStyle('body', parent=ss['Normal'], fontName='Helvetica',
                           fontSize=9.6, leading=13.8, textColor=INK,
                           alignment=TA_LEFT, spaceAfter=6)
S['bullet'] = ParagraphStyle('bullet', parent=S['body'], leftIndent=11,
                             bulletIndent=2, spaceAfter=3)
S['code'] = ParagraphStyle('code', parent=ss['Normal'], fontName='Courier',
                           fontSize=8.1, leading=11.2, textColor=INK,
                           backColor=BG, borderPadding=6, leftIndent=2,
                           rightIndent=2, spaceBefore=4, spaceAfter=7)
S['cell'] = ParagraphStyle('cell', parent=ss['Normal'], fontName='Helvetica',
                           fontSize=8.5, leading=11.5, textColor=INK)
S['cellb'] = ParagraphStyle('cellb', parent=S['cell'], fontName='Helvetica-Bold')
S['cellm'] = ParagraphStyle('cellm', parent=S['cell'], fontName='Courier', fontSize=8)
S['note'] = ParagraphStyle('note', parent=S['body'], fontSize=9.1, leading=13,
                           backColor=WARNBG, borderPadding=7, spaceBefore=5,
                           spaceAfter=8, leftIndent=2, rightIndent=2)
S['ok'] = ParagraphStyle('ok', parent=S['note'], backColor=OKBG)
S['foot'] = ParagraphStyle('foot', parent=ss['Normal'], fontName='Helvetica',
                           fontSize=7.6, textColor=MUTED)

story = []

def P(t, s='body'):   story.append(Paragraph(t, S[s]))
def H1(t):            story.append(Paragraph(t, S['h1']))
def H2(t):            story.append(Paragraph(t, S['h2']))
def H3(t):            story.append(Paragraph(t, S['h3']))
def SP(h=5):          story.append(Spacer(1, h))
def CODE(t):
    t = (t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
          .replace('\n', '<br/>').replace(' ', '&nbsp;'))
    story.append(Paragraph(t, S['code']))
def BUL(items):
    for it in items:
        story.append(Paragraph(it, S['bullet'], bulletText='•'))
    SP(4)

def TBL(header, rows, widths, mono_cols=()):
    data = [[Paragraph(h, S['cellb']) for h in header]]
    for r in rows:
        data.append([Paragraph(str(c), S['cellm'] if i in mono_cols else S['cell'])
                     for i, c in enumerate(r)])
    t = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BG),
        ('LINEBELOW', (0, 0), (-1, 0), 0.9, ACCENT),
        ('LINEBELOW', (0, 1), (-1, -2), 0.3, RULE),
        ('BOX', (0, 0), (-1, -1), 0.5, RULE),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 4.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    SP(9)

# ---------------------------------------------------------------- title
P("Clock-Domain-Crossing IP Library", 'title')
P("Design, Verification, Packaging and FPGA Implementation", 'sub')
P("HFT Tick-to-Trade Accelerator &nbsp;&middot;&nbsp; <b>cdc_integration</b> block design "
  "&nbsp;&middot;&nbsp; 30 July 2026", 'sub')
SP(4)
story.append(Table([['']], colWidths=[170*mm], rowHeights=[1.6],
                   style=TableStyle([('BACKGROUND', (0, 0), (-1, -1), ACCENT)])))
SP(12)

P("This report documents a hand-written clock-domain-crossing (CDC) primitive library "
  "built for a three-clock-domain HFT tick-to-trade design, together with its "
  "verification, Vivado IP packaging, block-design integration, timing-constraint "
  "strategy, and the post-route implementation results on a Xilinx UltraScale+ device. "
  "It also records the engineering problems encountered and how each was diagnosed and "
  "resolved, since several were subtle and are worth not re-learning.")

H1("1. Motivation and Domain Architecture")
P("The original tick-to-trade design ran entirely on a single clock. Real trading "
  "hardware does not: the line-side receive logic is tied to the Ethernet recovered "
  "clock, the trading core is pushed as fast as timing allows, and the host/CSR "
  "interface runs slower and asynchronously. Moving to three domains makes the design "
  "representative and forces every boundary to be handled explicitly.")

TBL(["Domain", "Clock", "Period", "Role"],
    [["RX-line", "clk_line", "6.400 ns (156.25 MHz)",
      "Models a 10G MAC receive datapath; ingests raw ITCH bytes"],
     ["Core", "clk_core", "4.000 ns (250.00 MHz)",
      "Parser, order book, strategy, risk checks, order emit"],
     ["Host / CSR", "clk_host", "8.000 ns (125.00 MHz)",
      "AXI-Lite register access, configuration, telemetry readback"]],
    [26*mm, 24*mm, 40*mm, 80*mm], mono_cols=(1, 2))

story.append(Paragraph(
    "<b>Honest scope note.</b> Inside the target FPGA shell there is no real Ethernet "
    "MAC available to this design, so the RX-line clock is a <i>modelled</i> auxiliary "
    "shell clock rather than a genuine recovered clock. The CDC logic and constraints "
    "are identical either way and transfer unchanged; only the clock source differs.",
    S['note']))

P("The existing module boundaries were already stream-shaped &mdash; AXI-Stream bytes in, "
  "AXI-Stream decisions out, AXI-Lite for control &mdash; so they form natural, clean "
  "crossing points rather than requiring the datapath to be restructured.")

H1("2. The Five CDC IPs")
P("Each block is hand-written rather than instantiated from Xilinx XPM. The XPM "
  "equivalents are documented as a production swap path, but building them by hand makes "
  "the failure modes explicit and demonstrable, which is the point of the exercise.")

TBL(["IP", "Crosses", "Mechanism", "Use for"],
    [["sync_2ff", "1 bit", "N-flop synchronizer chain",
      "Level-stable single-bit controls"],
     ["pulse_sync", "1 bit event", "Toggle + closed-loop acknowledge",
      "Single-cycle strobes between domains"],
     ["async_fifo", "Data stream", "Gray-coded pointers, dual-clock RAM",
      "Bulk data with elasticity"],
     ["axis_async_fifo", "AXI-Stream", "async_fifo + AXIS handshake wrapper",
      "Stream interfaces at IP boundaries"],
     ["handshake_mcp", "N-bit word", "Req/ack multi-cycle path",
      "Atomic configuration and telemetry words"]],
    [30*mm, 24*mm, 55*mm, 61*mm], mono_cols=(0,))

# ---- 2.1
H2("2.1 &nbsp;sync_2ff &mdash; single-bit synchronizer")
P("The workhorse primitive. It brings one asynchronous bit into the destination clock "
  "through a chain of <font face='Courier'>STAGES</font> flip-flops. The first flop may "
  "go metastable when its input changes near the sampling edge; the remaining flop(s) "
  "give that metastability time to resolve, so mean-time-between-failure grows "
  "exponentially with chain depth. Two stages is standard; three is used for very "
  "high-frequency or high-reliability crossings.")
CODE("""module sync_2ff #(
    parameter int STAGES   = 2,     // synchronizer depth (>= 2)
    parameter bit INIT_VAL = 1'b0   // reset / power-up value of the chain
)(
    input  logic clk, rst_n,        // destination domain
    input  logic d,                 // asynchronous bit (source domain)
    output logic q                  // synchronized bit (in `clk` domain)
);""")
story.append(Paragraph(
    "<b>Critical restriction.</b> Use this for SINGLE-BIT signals only. Instantiating one "
    "per bit of a bus is a classic and serious bug: each bit resolves independently, on "
    "different cycles, so the destination can momentarily observe a value that was never "
    "driven. Buses must cross via <font face='Courier'>async_fifo</font> (data) or "
    "<font face='Courier'>handshake_mcp</font> (configuration).", S['note']))
P("The chain carries the <font face='Courier'>ASYNC_REG</font> attribute in RTL, which "
  "tells synthesis and place-and-route that these are synchronizer flops: never merge or "
  "retime them away, and place them physically adjacent to maximise the settling window.")

# ---- 2.2
H2("2.2 &nbsp;pulse_sync &mdash; event crossing")
P("A single-cycle pulse cannot simply be synchronized: if the destination clock is "
  "slower, the pulse can fall entirely between two sampling edges and vanish. This block "
  "converts the event into a level change instead. A source-domain toggle flips on each "
  "request; the toggle is synchronized into the destination and edge-detected to "
  "regenerate a one-cycle strobe.")
P("A returning acknowledge toggle is synchronized back to the source, driving "
  "<font face='Courier'>src_busy</font>. This closes the loop: the source is told when "
  "the previous event has actually landed, so it cannot issue events faster than the "
  "destination can absorb them &mdash; the other classic pulse-crossing failure.")
CODE("""input  logic src_clk, src_rst_n, src_pulse;   output logic src_busy;
input  logic dst_clk, dst_rst_n;             output logic dst_pulse;""")

# ---- 2.3
H2("2.3 &nbsp;async_fifo &mdash; dual-clock data FIFO")
P("A dual-clock FIFO with a shared memory array, written in the source domain and read "
  "in the destination domain. The difficulty is not the storage but the pointer "
  "comparison: each side must know the other side's pointer to compute full and empty, "
  "and that pointer is a multi-bit value crossing a clock boundary.")
P("The pointers are therefore <b>Gray coded</b>. In Gray code exactly one bit changes per "
  "increment, so if the destination samples mid-transition it captures either the old "
  "pointer or the new one &mdash; never an invalid intermediate. This single property is "
  "what makes a multi-bit crossing safe, and it is why the pointer bits also require a "
  "bounded-skew timing constraint (Section 6).")
CODE("""parameter int WIDTH = 8, DEPTH = 16, STAGES = 2,
              AF_LEVEL = DEPTH-2, AE_LEVEL = 1;
wr_clk / wr_rst_n / wr_en / wr_data -> full,  almost_full
rd_clk / rd_rst_n / rd_en / rd_data -> empty, almost_empty""")
P("The FIFO is first-word fall-through: <font face='Courier'>rd_data</font> presents the "
  "head of the queue without needing a read strobe first. "
  "<font face='Courier'>almost_full</font> and <font face='Courier'>almost_empty</font> "
  "provide programmable back-pressure thresholds, which matter for a line-rate ingress "
  "buffer that must signal congestion before it actually overflows.")

# ---- 2.4
H2("2.4 &nbsp;axis_async_fifo &mdash; AXI-Stream wrapper")
P("A thin wrapper that presents <font face='Courier'>async_fifo</font> as a standard "
  "AXI-Stream slave/master pair, translating between "
  "<font face='Courier'>tvalid/tready</font> handshaking and the FIFO's "
  "<font face='Courier'>full/empty</font> flags, and carrying "
  "<font face='Courier'>tlast</font> alongside the data. This is the form actually used "
  "at design boundaries, because it drops directly into IP Integrator and connects to "
  "any AXIS producer or consumer without glue logic.")
CODE("""s_clk / s_rst_n : s_axis_tvalid, s_axis_tready, s_axis_tdata, s_axis_tlast
m_clk / m_rst_n : m_axis_tvalid, m_axis_tready, m_axis_tdata, m_axis_tlast""")

# ---- 2.5
H2("2.5 &nbsp;handshake_mcp &mdash; multi-cycle-path config crossing")
P("For a wide configuration word &mdash; a strategy selector, a risk threshold &mdash; a "
  "FIFO is overkill and per-bit synchronizers are wrong. This block uses a multi-cycle "
  "path (MCP) formulation: the source captures the word into a holding register and "
  "raises a request toggle; only that 1-bit toggle is synchronized. When the destination "
  "sees the request it samples the held data and returns an acknowledge toggle.")
P("The wide data bus itself crosses with <b>no synchronizer at all</b>, and that is "
  "correct by construction: the source holds it constant until acknowledged, so it is "
  "guaranteed stable for the entire round trip. It needs a bounded delay, not "
  "synchronization. Vivado reports this structure as CDC-15, which is the expected "
  "signature of a properly built MCP &mdash; a genuinely unprotected bus reports CDC-1 or "
  "CDC-2 (critical) instead.")
CODE("""parameter int WIDTH = 32, STAGES = 2;
src_clk: src_valid, src_data[WIDTH-1:0] -> src_ready
dst_clk: dst_valid (1-cycle) , dst_data[WIDTH-1:0] (held stable between updates)""")

H1("3. Functional Verification")
P("The library was verified before any Vivado work, using cocotb 2.0.1 driving Verilator "
  "5.036. Every block is exercised in <b>both clock-ratio directions</b> (fast source to "
  "slow destination and the reverse), because CDC bugs are frequently direction-specific "
  "&mdash; a design that works fast-to-slow can drop events slow-to-fast, and vice versa.")
CODE("$ cd sim && make cdc-regression\n   ...\n==== CDC regression PASSED ====   8 tests, 0 failures")
P("Independent Python golden models in <font face='Courier'>sim/golden/</font> "
  "(<font face='Courier'>async_fifo.py</font>, "
  "<font face='Courier'>handshake_mcp.py</font>) provide reference behaviour, so the "
  "testbench checks against an independently written model rather than against the RTL's "
  "own assumptions.")
H3("Verification lessons worth keeping")
BUL([
    "<b>Drive stimulus on the falling edge.</b> Under Verilator a signal written "
    "immediately before <font face='Courier'>await RisingEdge</font> is not visible at "
    "that edge. Driving on the falling edge guarantees stability at the sampling edge. "
    "This initially made a 2-flop synchronizer appear to take three cycles, and stopped "
    "toggle-based blocks from firing at all.",
    "<b>Sample one-cycle strobes on the falling edge</b> too; sampling on the rising edge "
    "is delta-cycle sensitive and flaky.",
    "<b>Beware last-beat cancellation.</b> A producer that asserts valid, exits its loop "
    "and deasserts before the committing edge silently loses the final beat.",
    "<b>First-word fall-through consumers</b> must record the current head and assert pop "
    "for the following edge; gating on the previous ready and sampling after the pop "
    "records the wrong beat and stalls.",
])

H1("4. IP Packaging")
P("Each block is packaged as a standalone Vivado IP with an out-of-context (OOC) "
  "synthesis mode, so it synthesizes independently and is cached. Two constraint files "
  "accompany each block, and the distinction between them is load-bearing.")
TBL(["File", "Packaged?", "Contents", "Why"],
    [["&lt;block&gt;.xdc", "Yes &mdash; USED_IN synthesis, implementation",
      "ASYNC_REG, set_max_delay -datapath_only",
      "Names no clock and no port, so it stays valid at any depth in any hierarchy"],
     ["&lt;block&gt;_ooc.xdc", "Only with USED_IN out_of_context",
      "create_clock on the block's own ports",
      "Describes the standalone world; valid only while the block is its own top"]],
    [34*mm, 40*mm, 46*mm, 50*mm], mono_cols=(0,))
story.append(Paragraph(
    "<b>The trap.</b> <font face='Courier'>USED_IN_out_of_context</font> is "
    "<i>exclusive</i>, not additive. A file carrying it does not apply in the integrated "
    "design at all &mdash; even though it still sits in the synthesis fileset. Tagging the "
    "OOC file correctly is what stops it creating shadow clocks; accidentally tagging the "
    "always-apply file the same way silently disables every CDC exception the IP carries, "
    "and no report anywhere says so.", S['note']))
P("Because that failure is invisible, a checker script "
  "(<font face='Courier'>ip/check_packaged_ip.py</font>) validates each packaged "
  "<font face='Courier'>component.xml</font> directly, without launching Vivado. It "
  "enforces that the always-apply XDC is shipped in the synthesis fileset and is not "
  "OOC-tagged, that the OOC file is either absent or correctly tagged, that clock "
  "interfaces carry FREQ_HZ and ASSOCIATED_RESET, that resets are ACTIVE_LOW, and that "
  "every source the block instantiates actually ships. It exits non-zero so it can gate a "
  "script.")

H1("5. Block-Design Integration")
P("The <font face='Courier'>cdc_top</font> block design instantiates all five IPs &mdash; "
  "six instances, with <font face='Courier'>axis_async_fifo</font> appearing twice &mdash; "
  "wired across the three domains to exercise every crossing direction.")
TBL(["Instance", "Source &rarr; Destination", "Purpose in the real design"],
    [["axis_async_fifo_0", "clk_line &rarr; clk_core", "ITCH byte-stream ingress"],
     ["axis_async_fifo_1", "clk_core &rarr; clk_host", "Decision/telemetry egress"],
     ["async_fifo_0", "clk_host &harr; clk_core", "Raw dual-clock buffering"],
     ["handshake_mcp_0", "clk_host &rarr; clk_core", "Strategy select, risk thresholds"],
     ["pulse_sync_0", "clk_host &harr; clk_core", "Host-issued strobes (GO, clear)"],
     ["sync_2ff_0", "&rarr; clk_core", "Quasi-static controls such as halt"]],
    [36*mm, 44*mm, 90*mm], mono_cols=(0, 1))

H1("6. Timing-Constraint Strategy")
P("This is the part of the work with the most engineering content, and the part that "
  "changed most during implementation.")
H2("6.1 &nbsp;Why not set_clock_groups")
P("The obvious constraint for three unrelated clocks is:")
CODE("set_clock_groups -asynchronous -group [get_clocks clk_line] \\\n"
     "                               -group [get_clocks clk_core] \\\n"
     "                               -group [get_clocks clk_host]")
P("It works, and the design closed timing with it. But it removes the cross-domain paths "
  "from analysis <b>entirely</b>, which has two consequences. First, it overrides every "
  "<font face='Courier'>set_max_delay</font>, so any datapath bound written elsewhere "
  "becomes inert. Second, and more seriously, the router is left with no delay target on "
  "those nets and is free to skew a multi-bit crossing arbitrarily.")
P("For a Gray-coded FIFO pointer that dismantles the safety argument. \"Only one bit "
  "changes per increment\" protects the destination only if the bits arrive within roughly "
  "one destination period of each other. With unbounded routing, a slow bit from one "
  "increment can land after a fast bit from the next, and the destination samples a "
  "pointer value that never existed &mdash; producing a wrong full/empty result and hence "
  "lost or duplicated data.")
story.append(Paragraph(
    "There is a second, softer argument. Under clock groups, a crossing nobody remembered "
    "to protect looks exactly as green as one that was carefully constrained, because "
    "neither is checked. Bounding the paths instead means an unconstrained crossing shows "
    "up as a timing violation. \"Green because verified\" is a materially stronger claim "
    "than \"green because ignored\".", S['note']))

H2("6.2 &nbsp;What was used instead")
P("Every ordered domain pair carries an explicit datapath bound, budgeted at one "
  "<font face='Courier'>clk_core</font> period &mdash; the fastest destination of the "
  "three, applied uniformly so the bound is conservative everywhere:")
CODE("set CDC_MAX 4.000\n"
     "set_max_delay -datapath_only -from [get_clocks clk_line] -to [get_clocks clk_core] $CDC_MAX\n"
     "set_max_delay -datapath_only -from [get_clocks clk_core] -to [get_clocks clk_line] $CDC_MAX\n"
     "set_max_delay -datapath_only -from [get_clocks clk_core] -to [get_clocks clk_host] $CDC_MAX\n"
     "set_max_delay -datapath_only -from [get_clocks clk_host] -to [get_clocks clk_core] $CDC_MAX\n"
     "set_max_delay -datapath_only -from [get_clocks clk_line] -to [get_clocks clk_host] $CDC_MAX\n"
     "set_max_delay -datapath_only -from [get_clocks clk_host] -to [get_clocks clk_line] $CDC_MAX")
P("<font face='Courier'>-datapath_only</font> instructs the timing engine to count only "
  "logic and routing delay and to ignore clock skew and uncertainty, which is the correct "
  "treatment for genuinely asynchronous paths. Xilinx UG903 recommends this form over "
  "clock groups and false paths on CDC nets for precisely the reasons above.")
H3("Why clock-to-clock rather than cell-to-cell")
P("An earlier revision enumerated each crossing by cell name "
  "(<font face='Courier'>wgray_reg</font> &rarr; <font face='Courier'>wq_reg</font> and so "
  "on). That fails in the out-of-context flow: while the top level is synthesized the IPs "
  "are black boxes, their internal cells do not yet exist, and every filter returns "
  "nothing:")
CODE("[Vivado 12-4739] set_max_delay: No valid object(s) found for '-from ...'")
P("Clocks exist at every stage, so a clock-to-clock bound applies during both synthesis "
  "and implementation &mdash; and as a bonus it covers every crossing between the pair, "
  "including any not enumerated by hand. Finer-grained per-crossing bounds still belong "
  "inside each IP's own XDC, where the cells <i>are</i> visible when that file is read.")

H1("7. Results")
H2("7.1 &nbsp;Post-route timing &mdash; all constraints met")
TBL(["Check", "Value", "Failing endpoints", "Total"],
    [["Setup (WNS)", "+2.097 ns", "0", "593"],
     ["Hold (WHS)", "+0.046 ns", "0", "527"],
     ["Pulse width (WPWS)", "+1.468 ns", "0", "275"]],
    [42*mm, 32*mm, 40*mm, 30*mm], mono_cols=(1,))
H2("7.2 &nbsp;Per clock domain")
TBL(["Clock", "Frequency", "Setup WNS", "Hold WHS", "Endpoints"],
    [["clk_core", "250.00 MHz", "+2.097 ns", "+0.050 ns", "365"],
     ["clk_line", "156.25 MHz", "+5.330 ns", "+0.051 ns", "95"],
     ["clk_host", "125.00 MHz", "+6.572 ns", "+0.046 ns", "67"]],
    [28*mm, 30*mm, 30*mm, 30*mm, 26*mm], mono_cols=(0, 1, 2, 3))
H2("7.3 &nbsp;Cross-domain crossings, bounded and met")
P("Every pair is analyzed against the 4.000 ns datapath budget and passes with "
  "approximately 3 ns of margin:")
TBL(["Source &rarr; Destination", "Slack", "Failing", "Endpoints"],
    [["clk_host &rarr; clk_core", "+2.884 ns", "0", "44"],
     ["clk_core &rarr; clk_host", "+3.009 ns", "0", "12"],
     ["clk_line &rarr; clk_core", "+3.583 ns", "0", "5"],
     ["clk_core &rarr; clk_line", "+3.587 ns", "0", "5"]],
    [50*mm, 30*mm, 25*mm, 30*mm], mono_cols=(1,))
H2("7.4 &nbsp;Routing, DRC and utilization")
BUL(["<b>Routing:</b> 389 of 389 routable nets fully routed, 0 nets with routing errors.",
     "<b>DRC:</b> no violations.",
     "<b>Utilization:</b> 108 CLB LUTs (36 as distributed RAM), 200 registers, 0 block RAM.",
     "<b>Device:</b> xcu200-fsgd2104, speed grade -2."])
H2("7.5 &nbsp;CDC report")
P("No CDC-1 or CDC-2 (critical) findings. All entries are CDC-3 (Info, single-bit "
  "synchronized with ASYNC_REG), CDC-6 (multi-bit synchronized with ASYNC_REG) or CDC-15 "
  "(clock-enable controlled structure).")
story.append(Paragraph(
    "<b>CDC-6 on the FIFO pointers is expected and is waived, not fixed.</b> Vivado flags "
    "any multi-bit bus through a synchronizer because that is normally the classic CDC "
    "bug. Here the bus is Gray coded, so a skewed capture yields either the old or the new "
    "pointer and never an invalid intermediate; the tool cannot infer the encoding from "
    "the netlist. The same report shows the genuinely single-bit pointer MSB as CDC-3 "
    "Info through the identical synchronizer &mdash; the classification differs purely on "
    "bit width. CDC-15 on the handshake_mcp data bus is likewise the correct signature of "
    "a multi-cycle path.", S['ok']))

H1("8. Problems Encountered and Resolutions")
P("Several of these were subtle, cost real time, and are worth recording.")

H2("8.1 &nbsp;Vivado repeatedly killed by memory exhaustion")
P("<b>Symptom:</b> Vivado died partway through synthesis, leaving stale "
  "<font face='Courier'>__synthesis_is_running__</font> markers.")
P("<b>Cause:</b> the build machine has 16 cores but only 17 GB of RAM. Vivado sizes its "
  "default parallel job count from core count, so it launched all six out-of-context IP "
  "runs simultaneously. Each peaked around 3.6 GB &mdash; roughly 21 GB against 17 GB "
  "available. Every log ended with <font face='Courier'>free physical</font> under 250 MB. "
  "The memory is almost entirely the UltraScale+ device database, not the user logic: a "
  "trivial two-flop synchronizer peaked at 3.1 GB, essentially the same as the largest "
  "block.")
P("<b>Resolution:</b> run the OOC synthesis runs strictly serially "
  "(<font face='Courier'>launch_runs -jobs 1</font> with "
  "<font face='Courier'>wait_on_run</font> between each) and run headless so the GUI's "
  "own 1&ndash;2 GB is not competing. Full build time was about 13 minutes. Note that the "
  "GUI's <i>Run Synthesis</i> button relaunches every out-of-date run at its own default "
  "job count and ignores any previously set value, so the serial script is the only "
  "reliable path on this machine.")

H2("8.2 &nbsp;FIFO memory inferred as flip-flops instead of RAM")
P("<b>Symptom:</b> <font face='Courier'>[Synth 8-4767] Trying to implement RAM 'mem_reg' "
  "in registers. Block RAM or DRAM implementation is not possible &mdash; RAM is sensitive "
  "to asynchronous reset signal.</font>")
P("<b>Cause:</b> the FIFO array write sat inside an "
  "<font face='Courier'>always_ff</font> block that carried an asynchronous reset. Block "
  "RAM and LUTRAM primitives have no asynchronous reset on the array, so synthesis could "
  "not map it and fell back to discrete registers.")
P("<b>Resolution:</b> move the array write into its own reset-free "
  "<font face='Courier'>always_ff @(posedge wr_clk)</font> block, which is the canonical "
  "inference template. The storage now maps to distributed RAM (36 LUTs, visible in the "
  "final utilization). At this FIFO depth the difference is small &mdash; roughly 512 bits "
  "&mdash; but it would dominate area and timing at production depths.")

H2("8.3 &nbsp;CDC verified against the wrong clocks")
P("<b>Symptom:</b> <font face='Courier'>report_cdc</font> listed crossings between "
  "clocks named <font face='Courier'>wr_clk</font>, <font face='Courier'>s_clk</font> and "
  "<font face='Courier'>src_clk</font> &mdash; the IPs' own port names &mdash; rather than "
  "<font face='Courier'>clk_line</font>, <font face='Courier'>clk_core</font> and "
  "<font face='Courier'>clk_host</font>.")
P("<b>Cause:</b> each packaged IP shipped an OOC constraint file performing "
  "<font face='Courier'>create_clock</font> on the block's own ports. Once the IP is "
  "instantiated those ports are internal pins, and the clocks became shadow copies "
  "competing with the real top-level definitions. Three of the six instances were being "
  "analyzed against these shadow clocks and therefore were never verified at their actual "
  "frequencies, while the report still looked clean.")
P("<b>Resolution:</b> tag the OOC file <font face='Courier'>USED_IN out_of_context</font> "
  "so it applies only during the IP's own standalone run. This is also the origin of the "
  "packaging checker described in Section 4 &mdash; the failure was invisible in every "
  "report, so it needed a dedicated test.")

H2("8.4 &nbsp;Two constraint gaps hidden by the clock groups")
P("Removing <font face='Courier'>set_clock_groups</font> exposed two crossings that had "
  "never actually been constrained. Neither was visible while the paths were excluded "
  "from analysis.")
BUL(["<b>The Gray pointer MSB was unconstrained.</b> The Gray-code MSB equals the binary "
     "MSB, so synthesis drives that bit straight from the binary counter and no "
     "corresponding <font face='Courier'>gray_reg</font> cell exists. A filter matching "
     "only <font face='Courier'>*gray_reg*</font> silently missed one bit of every "
     "pointer bus &mdash; precisely the bit whose skew the constraint exists to bound.",
     "<b>The handshake_mcp request and acknowledge toggles had no delay bound at all</b>, "
     "only the ASYNC_REG attribute."])
P("Both are fixed in the IP-level constraint files. This is the clearest practical "
  "argument for bounding rather than ignoring: switching the constraint style is what "
  "surfaced them.")

H2("8.5 &nbsp;XDC language restrictions")
P("Two Vivado behaviours are worth stating plainly, because both fail quietly:")
BUL(["An <font face='Courier'>.xdc</font> accepts constraint commands plus basic "
     "<font face='Courier'>set</font>/<font face='Courier'>expr</font> only. "
     "<font face='Courier'>proc</font>, <font face='Courier'>puts</font>, "
     "<font face='Courier'>if</font> and <font face='Courier'>foreach</font> are rejected "
     "with <font face='Courier'>[Designutils 20-1307]</font> &mdash; and Vivado then skips "
     "them and continues, so the run appears to succeed with the constraints simply "
     "absent. Real Tcl must live in a <font face='Courier'>.tcl</font> script.",
     "A top-level XDC cannot reference cells inside an out-of-context IP, because those "
     "cells do not exist while the top level is synthesized. The accompanying "
     "<font face='Courier'>[Project 1-486] Could not resolve non-primitive black box "
     "cell</font> messages are normal for the OOC flow and are not errors."])

H1("9. Known Gaps and Next Steps")
BUL(["<b>Re-package the IPs so the library is self-sufficient.</b> The CDC bounds "
     "currently live in the top-level constraint file. For the blocks to be genuinely "
     "reusable, each IP should carry its own exceptions with the correct "
     "<font face='Courier'>USED_IN</font> setting.",
     "<b>Add set_bus_skew to the pointer buses.</b> <font face='Courier'>report_bus_skew"
     "</font> currently reports no bus-skew constraints. "
     "<font face='Courier'>set_max_delay</font> bounds each bit's absolute delay, which "
     "bounds skew indirectly; <font face='Courier'>set_bus_skew</font> states the "
     "relative-skew requirement directly and is what Xilinx's own XPM_CDC uses alongside "
     "max_delay.",
     "<b>Phase 2 integration.</b> Build the multi-clock tick-to-trade top level, placing "
     "these blocks on real datapaths rather than the present scaffold. Note that the "
     "current block design drives every IP input from a top-level port, so it validates "
     "each IP's <i>internal</i> CDC structure; system-level crossings can only be verified "
     "once real logic drives them.",
     "<b>Deepen the FIFOs.</b> Depth is at the default 16. A line-rate ingress buffer "
     "needs considerably more, and that is also where the RAM-inference fix of Section 8.2 "
     "starts to matter materially."])

SP(10)
story.append(Table([['']], colWidths=[170*mm], rowHeights=[0.8],
                   style=TableStyle([('BACKGROUND', (0, 0), (-1, -1), RULE)])))
SP(5)
P("Generated 30 July 2026 &nbsp;&middot;&nbsp; Vivado 2025.2 &nbsp;&middot;&nbsp; "
  "Verilator 5.036 &nbsp;&middot;&nbsp; cocotb 2.0.1 &nbsp;&middot;&nbsp; "
  "device xcu200-fsgd2104", 'foot')


class Doc(BaseDocTemplate):
    def afterPage(self):
        c, n = self.canv, self.page
        c.saveState()
        c.setFont('Helvetica', 7.6)
        c.setFillColor(MUTED)
        c.drawString(20*mm, 12*mm, "CDC IP Library — HFT Tick-to-Trade")
        c.drawRightString(190*mm, 12*mm, "%d" % n)
        c.setStrokeColor(RULE)
        c.setLineWidth(0.4)
        c.line(20*mm, 15*mm, 190*mm, 15*mm)
        c.restoreState()


doc = Doc(OUT, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm,
          topMargin=18*mm, bottomMargin=20*mm,
          title="CDC IP Library and Integration",
          author="Abhishek")
doc.addPageTemplates([PageTemplate(id='n', frames=[
    Frame(20*mm, 20*mm, 170*mm, 259*mm, id='f',
          leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)])])
doc.build(story)
print("wrote", OUT, os.path.getsize(OUT), "bytes")
