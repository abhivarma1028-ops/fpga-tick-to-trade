// mc_glue — the non-CDC glue logic of the 3-domain tick-to-trade top
// ===========================================================================
// A block design can instantiate and wire blocks, but it cannot draw an
// always_ff. The multi-clock top contains a little logic alongside its
// instances -- a config re-send FSM, two sticky overflow flags, a bit-slice and
// a gate -- and without it the canvas would silently be missing the config load
// and the overflow detection. This block is that logic.
//
// IMPORTANT: there is NO clock domain crossing inside this block. Every piece is
// single-domain; it just happens to contain logic for two different domains.
// The host registers are driven only by host-domain signals and the core
// registers only by core-domain signals. All real crossings live in the CDC
// blocks around it, which is where they belong and where they are constrained.
// If a path between clk_host and clk_core ever appears in here, that is a bug.
//
// PORT NAMING: deliberately avoids *_tvalid / *_tready / s_axi* / s_axil*.
// Vivado's IP packager infers bus interfaces from port names, and a partial set
// of AXI-looking names makes it invent a bogus interface -- it did exactly that,
// turning dec_tvalid/dec_tready into a phantom AXI-Stream channel called 'dec'.
// This block exposes loose wires on purpose.
//
// The AXI-Lite register path does NOT pass through here. In the block design it
// crosses clk_host <-> clk_core through a Xilinx axi_clock_converter, which
// connects interface-to-interface -- the packaged rtl_brain exposes s_axil as a
// single AXI-MM interface, so its individual pins are not reachable for the
// per-channel FIFO scheme the flat RTL top uses.
// ===========================================================================

module mc_glue #(
    parameter int CFG_W = 32     // width of the config word carried by handshake_mcp
)(
    // ================= host domain =================
    input  logic              clk_host,
    input  logic              rst_host_n,

    // strategy select in, config word out to handshake_mcp (host->core)
    input  logic [1:0]        strat_sel,
    input  logic              cfg_ready,      // <- u_cfg.src_ready
    output logic              cfg_valid,      // -> u_cfg.src_valid
    output logic [CFG_W-1:0]  cfg_data,       // -> u_cfg.src_data

    // ================= core domain =================
    input  logic              clk_core,
    input  logic              rst_core_n,

    // Config word arriving from handshake_mcp, sliced for the core. Only the
    // bottom 2 bits are used today; the rest is reserved so thresholds can be
    // added without changing the crossing, so the unused upper bits are
    // deliberate rather than an oversight.
    // verilator lint_off UNUSEDSIGNAL
    input  logic [CFG_W-1:0]  cfg_word_core,  // <- u_cfg.dst_data
    // verilator lint_on UNUSEDSIGNAL
    output logic [1:0]        strat_sel_core, // -> u_core.strat_sel

    // risk reject event: only launch when the crossing is free
    input  logic              risk_reject_core, // <- u_core.risk_reject
    input  logic              risk_evt_ready,   // <- u_risk_evt.src_ready
    output logic              risk_evt_valid,   // -> u_risk_evt.src_valid

    // decision egress back-pressure watch
    input  logic              dec_valid,        // <- u_core.m_axis_tvalid
    input  logic              dec_accept,       // <- u_egress.s_axis_tready

    // sticky flags, to be synchronised to the host by sync_2ff
    output logic              decision_overflow_core,
    output logic              risk_evt_overflow_core
);

    // ---------------- host: config word + re-send FSM ----------------
    // strat_sel is MULTI-BIT, so it must not be crossed with one synchronizer
    // per bit -- the bits would resolve on different cycles and the core could
    // momentarily select a strategy that was never requested. It rides in a
    // config word through handshake_mcp, which moves the whole word atomically.
    // The spare bits are deliberate: thresholds and other knobs can be added
    // later without touching the crossing.
    assign cfg_data = {{(CFG_W-2){1'b0}}, strat_sel};

    logic [CFG_W-1:0] cfg_sent;
    logic             cfg_load_pending;

    // Re-send whenever the config changes and the crossing is free.
    // cfg_load_pending forces one send after reset, otherwise a config that
    // happens to equal the reset value would never be transferred at all.
    always_ff @(posedge clk_host or negedge rst_host_n) begin
        if (!rst_host_n) begin
            cfg_valid        <= 1'b0;
            cfg_sent         <= '0;
            cfg_load_pending <= 1'b1;
        end else if (cfg_ready && (cfg_load_pending || cfg_data != cfg_sent)) begin
            cfg_valid        <= 1'b1;
            cfg_sent         <= cfg_data;
            cfg_load_pending <= 1'b0;
        end else begin
            cfg_valid        <= 1'b0;
        end
    end

    // ---------------- core: config slice ----------------
    assign strat_sel_core = cfg_word_core[1:0];

    // ---------------- core: risk event launch ----------------
    assign risk_evt_valid = risk_reject_core && risk_evt_ready;

    // ---------------- core: sticky overflow flags ----------------
    // The core emits one beat per decision and does not honour back-pressure, so
    // a full egress FIFO loses the beat. Losing a trade decision silently would
    // be the worst failure mode in this design, so it is detected and latched.
    always_ff @(posedge clk_core or negedge rst_core_n) begin
        if (!rst_core_n)                   decision_overflow_core <= 1'b0;
        else if (dec_valid && !dec_accept) decision_overflow_core <= 1'b1;
    end

    // Rejections arriving faster than the handshake round trip are dropped;
    // flag that rather than hide it.
    always_ff @(posedge clk_core or negedge rst_core_n) begin
        if (!rst_core_n)                              risk_evt_overflow_core <= 1'b0;
        else if (risk_reject_core && !risk_evt_ready) risk_evt_overflow_core <= 1'b1;
    end

endmodule
