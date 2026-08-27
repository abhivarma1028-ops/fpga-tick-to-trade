// tick_to_trade_mc_top — multi-clock-domain wrapper around tick_to_trade_top
// ===========================================================================
// Phase 2 of the CDC work. The single-clock `tick_to_trade_top` is left exactly
// as it is (its regression still runs against it); this module wraps it and
// moves every external interface onto the clock it would really live on:
//
//   clk_line  156.25 MHz   RX-line domain. ITCH bytes arrive here, at the pace
//                          of the (modelled) 10G MAC receive datapath.
//   clk_core  250.00 MHz   The trading core itself: parser, book, strategy,
//                          risk, order emit. Pushed as fast as timing allows.
//   clk_host  125.00 MHz   Host/CSR domain. Decision egress, AXI-Lite register
//                          access, kill switch, strategy selection.
//
// Every boundary between them is crossed with a block from rtl/cdc/, and the
// choice of block per boundary is the whole point of the exercise:
//
//   byte / decision / AXI-Lite streams  -> axis_async_fifo  (elastic, Gray ptr)
//   halt, sticky status flags           -> sync_2ff         (1 bit, level)
//   strategy select + thresholds        -> handshake_mcp    (atomic wide word)
//   risk reject event + its reason code -> handshake_mcp    (event AND data)
//
// NOTE ON RESETS: each domain takes its own active-low async reset, and this
// module synchronizes each one itself with a per-domain reset_sync (async
// assert, synchronous release) -- see section 0. It no longer assumes the
// shell's reset tree releases them synchronously, so all three inputs may be
// driven from a single un-synchronized source.
// ===========================================================================

module tick_to_trade_mc_top #(
    // All depths must be powers of two (async_fifo requirement).
    parameter int IN_DEPTH   = 512,  // line->core ITCH byte buffer: absorbs bursts
    parameter int OUT_DEPTH  = 64,   // core->host decision buffer
    parameter int CSR_DEPTH  = 4     // AXI-Lite channels: shallow, low rate
)(
    // ---------------- RX-line domain ----------------
    input  logic        clk_line,
    input  logic        rst_line_n,
    input  logic        s_axis_tvalid,
    output logic        s_axis_tready,
    input  logic [7:0]  s_axis_tdata,
    input  logic        s_axis_tlast,

    // ---------------- core domain ----------------
    input  logic        clk_core,
    input  logic        rst_core_n,

    // ---------------- host / CSR domain ----------------
    input  logic        clk_host,
    input  logic        rst_host_n,

    // kill switch and strategy selection (host-driven configuration)
    input  logic        halt,
    input  logic [1:0]  strat_sel,

    // risk rejection notification, re-timed into the host domain
    output logic        risk_reject,   // 1-cycle strobe in clk_host
    output logic [2:0]  risk_reason,   // valid when risk_reject is high

    // sticky overflow flags (host domain, level) — see the note at u_egress
    output logic        decision_overflow, // a decision was dropped: FIFO full
    output logic        risk_evt_overflow, // a reject event was dropped

    // decision stream out
    output logic        m_axis_tvalid,
    input  logic        m_axis_tready,
    output logic [71:0] m_axis_tdata,  // {action[7:0], price[31:0], size[31:0]}
    output logic        m_axis_tlast,

    // AXI-Lite slave — latency counter histogram readout
    input  logic [8:0]  s_axil_awaddr,
    input  logic        s_axil_awvalid,
    output logic        s_axil_awready,
    input  logic [31:0] s_axil_wdata,
    input  logic [3:0]  s_axil_wstrb,
    input  logic        s_axil_wvalid,
    output logic        s_axil_wready,
    output logic [1:0]  s_axil_bresp,
    output logic        s_axil_bvalid,
    input  logic        s_axil_bready,
    input  logic [8:0]  s_axil_araddr,
    input  logic        s_axil_arvalid,
    output logic        s_axil_arready,
    output logic [31:0] s_axil_rdata,
    output logic [1:0]  s_axil_rresp,
    output logic        s_axil_rvalid,
    input  logic        s_axil_rready
);

    // =======================================================================
    // 0. Reset synchronizers -- one per domain
    // =======================================================================
    // The three rst_*_n inputs arrive asynchronously. Asserting a reset
    // asynchronously is correct and desirable, but RELEASING it asynchronously
    // is not: if it de-asserts near a clock edge, some flops in the domain see
    // the release and some do not, and the design leaves reset in a state that
    // was never designed for. Each domain therefore gets its own reset_sync --
    // async assert, synchronous release -- and everything below uses the
    // synchronized version.
    //
    // Without these, report_cdc flags every reset endpoint in the design:
    // 4375 x CDC-7 "Asynchronous reset unknown CDC circuitry" plus 441 CDC-1
    // in the block-design build.
    //
    // One per domain, never shared -- sharing would reintroduce exactly the
    // problem they exist to solve.
    logic rst_line_s_n, rst_core_s_n, rst_host_s_n;

    reset_sync #(.STAGES(3)) u_rst_line (
        .clk (clk_line), .arst_n (rst_line_n), .rst_n (rst_line_s_n));
    reset_sync #(.STAGES(3)) u_rst_core (
        .clk (clk_core), .arst_n (rst_core_n), .rst_n (rst_core_s_n));
    reset_sync #(.STAGES(3)) u_rst_host (
        .clk (clk_host), .arst_n (rst_host_n), .rst_n (rst_host_s_n));

    // =======================================================================
    // 1. ITCH byte stream : clk_line -> clk_core
    // =======================================================================
    // The deepest crossing in the design. The line side cannot be back-pressured
    // in reality, so this FIFO is what absorbs a burst while the core catches up;
    // IN_DEPTH is the elasticity budget.
    logic        core_s_tvalid, core_s_tready, core_s_tlast;
    logic [7:0]  core_s_tdata;

    axis_async_fifo #(.TDATA_W(8), .DEPTH(IN_DEPTH)) u_ingress (
        .s_clk         (clk_line),
        .s_rst_n       (rst_line_s_n),
        .s_axis_tvalid (s_axis_tvalid),
        .s_axis_tready (s_axis_tready),
        .s_axis_tdata  (s_axis_tdata),
        .s_axis_tlast  (s_axis_tlast),
        .m_clk         (clk_core),
        .m_rst_n       (rst_core_s_n),
        .m_axis_tvalid (core_s_tvalid),
        .m_axis_tready (core_s_tready),
        .m_axis_tdata  (core_s_tdata),
        .m_axis_tlast  (core_s_tlast)
    );

    // =======================================================================
    // 2. Host configuration : clk_host -> clk_core
    // =======================================================================
    // `halt` is a single quasi-static level, so a plain synchronizer is exactly
    // right: it does not matter which cycle it lands on, only that it lands
    // cleanly.
    logic halt_core;
    sync_2ff #(.STAGES(2), .INIT_VAL(1'b1)) u_halt (   // reset to HALTED (safe)
        .clk   (clk_core),
        .rst_n (rst_core_s_n),
        .d     (halt),
        .q     (halt_core)
    );

    // `strat_sel` is MULTI-BIT, so it must NOT be crossed with one sync_2ff per
    // bit — the bits would resolve on different cycles and the core could
    // momentarily select a strategy that was never requested. It is carried in a
    // 32-bit configuration word through handshake_mcp instead, which crosses the
    // whole word atomically. The spare bits are deliberate: thresholds and other
    // knobs can be added later without touching the crossing.
    localparam int CFG_W = 32;

    logic [CFG_W-1:0] cfg_word_host, cfg_sent_host;
    logic             cfg_valid_host, cfg_ready_host, cfg_load_pending;

    assign cfg_word_host = {{(CFG_W-2){1'b0}}, strat_sel};

    // Re-send the configuration whenever it changes and the crossing is free.
    // `cfg_load_pending` forces one send after reset, otherwise a config that
    // happens to equal the reset value would never be transferred at all.
    always_ff @(posedge clk_host or negedge rst_host_s_n) begin
        if (!rst_host_s_n) begin
            cfg_valid_host   <= 1'b0;
            cfg_sent_host    <= '0;
            cfg_load_pending <= 1'b1;
        end else if (cfg_ready_host &&
                     (cfg_load_pending || cfg_word_host != cfg_sent_host)) begin
            cfg_valid_host   <= 1'b1;
            cfg_sent_host    <= cfg_word_host;
            cfg_load_pending <= 1'b0;
        end else begin
            cfg_valid_host   <= 1'b0;
        end
    end

    logic [CFG_W-1:0] cfg_word_core;
    // verilator lint_off PINCONNECTEMPTY
    handshake_mcp #(.WIDTH(CFG_W), .STAGES(2)) u_cfg (
        .src_clk   (clk_host),
        .src_rst_n (rst_host_s_n),
        .src_valid (cfg_valid_host),
        .src_data  (cfg_word_host),
        .src_ready (cfg_ready_host),
        .dst_clk   (clk_core),
        .dst_rst_n (rst_core_s_n),
        .dst_valid (),                 // level-held config; the pulse is unused
        .dst_data  (cfg_word_core)
    );
    // verilator lint_on PINCONNECTEMPTY

    logic [1:0] strat_sel_core;
    assign strat_sel_core = cfg_word_core[1:0];

    // =======================================================================
    // 3. The trading core, entirely inside clk_core
    // =======================================================================
    logic        core_m_tvalid, core_m_tready, core_m_tlast;
    logic [71:0] core_m_tdata;
    logic        risk_reject_core;
    logic [2:0]  risk_reason_core;

    // AXI-Lite as seen by the core (already re-timed into clk_core, section 5)
    logic [8:0]  c_awaddr;  logic c_awvalid, c_awready;
    logic [31:0] c_wdata;   logic [3:0] c_wstrb;
    logic        c_wvalid,  c_wready;
    logic [1:0]  c_bresp;   logic c_bvalid, c_bready;
    logic [8:0]  c_araddr;  logic c_arvalid, c_arready;
    logic [31:0] c_rdata;   logic [1:0] c_rresp;
    logic        c_rvalid,  c_rready;

    tick_to_trade_top u_core (
        .clk            (clk_core),
        .rst_n          (rst_core_s_n),

        .s_axis_tvalid  (core_s_tvalid),
        .s_axis_tready  (core_s_tready),
        .s_axis_tdata   (core_s_tdata),
        .s_axis_tlast   (core_s_tlast),

        .halt           (halt_core),
        .risk_reject    (risk_reject_core),
        .risk_reason    (risk_reason_core),
        .strat_sel      (strat_sel_core),

        .m_axis_tvalid  (core_m_tvalid),
        .m_axis_tready  (core_m_tready),
        .m_axis_tdata   (core_m_tdata),
        .m_axis_tlast   (core_m_tlast),

        .s_axil_awaddr  (c_awaddr),
        .s_axil_awvalid (c_awvalid),
        .s_axil_awready (c_awready),
        .s_axil_wdata   (c_wdata),
        .s_axil_wstrb   (c_wstrb),
        .s_axil_wvalid  (c_wvalid),
        .s_axil_wready  (c_wready),
        .s_axil_bresp   (c_bresp),
        .s_axil_bvalid  (c_bvalid),
        .s_axil_bready  (c_bready),
        .s_axil_araddr  (c_araddr),
        .s_axil_arvalid (c_arvalid),
        .s_axil_arready (c_arready),
        .s_axil_rdata   (c_rdata),
        .s_axil_rresp   (c_rresp),
        .s_axil_rvalid  (c_rvalid),
        .s_axil_rready  (c_rready)
    );

    // =======================================================================
    // 4. Decision stream and risk events : clk_core -> clk_host
    // =======================================================================
    // The core emits one beat per decision and does NOT honour back-pressure
    // (see tick_to_trade_top: m_axis_tready is explicitly unused). If this FIFO
    // is ever full the beat is lost, so rather than pretend otherwise we detect
    // it and latch a sticky flag. Losing a trade decision silently would be the
    // worst possible failure mode in this design.
    axis_async_fifo #(.TDATA_W(72), .DEPTH(OUT_DEPTH)) u_egress (
        .s_clk         (clk_core),
        .s_rst_n       (rst_core_s_n),
        .s_axis_tvalid (core_m_tvalid),
        .s_axis_tready (core_m_tready),
        .s_axis_tdata  (core_m_tdata),
        .s_axis_tlast  (core_m_tlast),
        .m_clk         (clk_host),
        .m_rst_n       (rst_host_s_n),
        .m_axis_tvalid (m_axis_tvalid),
        .m_axis_tready (m_axis_tready),
        .m_axis_tdata  (m_axis_tdata),
        .m_axis_tlast  (m_axis_tlast)
    );

    logic decision_overflow_core;
    always_ff @(posedge clk_core or negedge rst_core_s_n) begin
        if (!rst_core_s_n)                            decision_overflow_core <= 1'b0;
        else if (core_m_tvalid && !core_m_tready)   decision_overflow_core <= 1'b1;
    end

    // A sticky level is a single bit that only ever goes 0->1, so a plain
    // synchronizer is sufficient and correct here.
    sync_2ff #(.STAGES(2)) u_dec_ovf (
        .clk (clk_host), .rst_n (rst_host_s_n),
        .d   (decision_overflow_core), .q (decision_overflow)
    );

    // A risk rejection is an EVENT that carries DATA (the reason code). Crossing
    // the event with pulse_sync and the reason separately would race: the strobe
    // could arrive before the reason settled. handshake_mcp moves both together,
    // and its dst_valid is exactly the re-timed event strobe.
    logic risk_evt_ready_core;
    handshake_mcp #(.WIDTH(3), .STAGES(2)) u_risk_evt (
        .src_clk   (clk_core),
        .src_rst_n (rst_core_s_n),
        .src_valid (risk_reject_core && risk_evt_ready_core),
        .src_data  (risk_reason_core),
        .src_ready (risk_evt_ready_core),
        .dst_clk   (clk_host),
        .dst_rst_n (rst_host_s_n),
        .dst_valid (risk_reject),
        .dst_data  (risk_reason)
    );

    // Rejections arriving faster than the handshake round trip are dropped;
    // flag that rather than hide it.
    logic risk_evt_overflow_core;
    always_ff @(posedge clk_core or negedge rst_core_s_n) begin
        if (!rst_core_s_n)                                  risk_evt_overflow_core <= 1'b0;
        else if (risk_reject_core && !risk_evt_ready_core) risk_evt_overflow_core <= 1'b1;
    end

    sync_2ff #(.STAGES(2)) u_risk_ovf (
        .clk (clk_host), .rst_n (rst_host_s_n),
        .d   (risk_evt_overflow_core), .q (risk_evt_overflow)
    );

    // =======================================================================
    // 5. AXI-Lite : clk_host <-> clk_core
    // =======================================================================
    // Each AXI channel is an independent valid/ready stream, so each crosses in
    // its own async FIFO. The channels are deliberately NOT re-synchronised
    // against each other: AXI does not require it, and the core's own AXI-Lite
    // slave already enforces the ordering it needs.
    //
    // These are shallow (CSR_DEPTH) — register access is low rate and latency
    // here is irrelevant, unlike the datapath above.
    localparam logic ONE = 1'b1;

    // ---- write address : host -> core ----
    /* verilator lint_off PINCONNECTEMPTY */
    axis_async_fifo #(.TDATA_W(9), .DEPTH(CSR_DEPTH)) u_axil_aw (
        .s_clk(clk_host), .s_rst_n(rst_host_s_n),
        .s_axis_tvalid(s_axil_awvalid), .s_axis_tready(s_axil_awready),
        .s_axis_tdata (s_axil_awaddr),  .s_axis_tlast (ONE),
        .m_clk(clk_core), .m_rst_n(rst_core_s_n),
        .m_axis_tvalid(c_awvalid), .m_axis_tready(c_awready),
        .m_axis_tdata (c_awaddr),  .m_axis_tlast ()
    );

    // ---- write data : host -> core ---- ({wstrb, wdata})
    logic [35:0] w_host, w_core;
    assign w_host = {s_axil_wstrb, s_axil_wdata};
    assign {c_wstrb, c_wdata} = w_core;

    axis_async_fifo #(.TDATA_W(36), .DEPTH(CSR_DEPTH)) u_axil_w (
        .s_clk(clk_host), .s_rst_n(rst_host_s_n),
        .s_axis_tvalid(s_axil_wvalid), .s_axis_tready(s_axil_wready),
        .s_axis_tdata (w_host),        .s_axis_tlast (ONE),
        .m_clk(clk_core), .m_rst_n(rst_core_s_n),
        .m_axis_tvalid(c_wvalid), .m_axis_tready(c_wready),
        .m_axis_tdata (w_core),   .m_axis_tlast ()
    );

    // ---- write response : core -> host ----
    axis_async_fifo #(.TDATA_W(2), .DEPTH(CSR_DEPTH)) u_axil_b (
        .s_clk(clk_core), .s_rst_n(rst_core_s_n),
        .s_axis_tvalid(c_bvalid), .s_axis_tready(c_bready),
        .s_axis_tdata (c_bresp),  .s_axis_tlast (ONE),
        .m_clk(clk_host), .m_rst_n(rst_host_s_n),
        .m_axis_tvalid(s_axil_bvalid), .m_axis_tready(s_axil_bready),
        .m_axis_tdata (s_axil_bresp),  .m_axis_tlast ()
    );

    // ---- read address : host -> core ----
    axis_async_fifo #(.TDATA_W(9), .DEPTH(CSR_DEPTH)) u_axil_ar (
        .s_clk(clk_host), .s_rst_n(rst_host_s_n),
        .s_axis_tvalid(s_axil_arvalid), .s_axis_tready(s_axil_arready),
        .s_axis_tdata (s_axil_araddr),  .s_axis_tlast (ONE),
        .m_clk(clk_core), .m_rst_n(rst_core_s_n),
        .m_axis_tvalid(c_arvalid), .m_axis_tready(c_arready),
        .m_axis_tdata (c_araddr),  .m_axis_tlast ()
    );

    // ---- read data : core -> host ---- ({rresp, rdata})
    logic [33:0] r_core, r_host;
    assign r_core = {c_rresp, c_rdata};
    assign {s_axil_rresp, s_axil_rdata} = r_host;

    axis_async_fifo #(.TDATA_W(34), .DEPTH(CSR_DEPTH)) u_axil_r (
        .s_clk(clk_core), .s_rst_n(rst_core_s_n),
        .s_axis_tvalid(c_rvalid), .s_axis_tready(c_rready),
        .s_axis_tdata (r_core),   .s_axis_tlast (ONE),
        .m_clk(clk_host), .m_rst_n(rst_host_s_n),
        .m_axis_tvalid(s_axil_rvalid), .m_axis_tready(s_axil_rready),
        .m_axis_tdata (r_host),        .m_axis_tlast ()
    );
    /* verilator lint_on PINCONNECTEMPTY */

endmodule
