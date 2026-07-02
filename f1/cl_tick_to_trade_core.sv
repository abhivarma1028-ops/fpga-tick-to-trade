// cl_tick_to_trade_core.sv — F1 Custom Logic core (Shell-independent)
//
// Wraps tick_to_trade_top behind ONE AXI-Lite (the F1 Shell's OCL/BAR0 bus) so
// the host drives the whole pipeline through registers — no separate DMA engine
// needed for Phase-3 bring-up (equivalence + latency, not throughput). The real
// Shell wrapper (cl_tick_to_trade.sv) just maps sh_ocl_* onto this module's ocl_*
// ports; this core is what the cocotb test exercises directly.
//
// OCL register map (extends rtl/latency_counter.sv; see f1/register_map.md):
//   0x000-0x1FF : passthrough to tick_to_trade_top.s_axil (latency histogram)
//   0x200  (W)  : ITCH ingress — wdata[7:0]=byte, wdata[8]=tlast (pushes s_axis)
//   0x204  (R)  : status — bit0 ingress_full, bit1 ingress_empty,
//                          bit2 egress_overflow, [15:8] decision_count
//   0x2FC  (R)  : decision_count
//   0x300+16*i  : decision i — +0 action, +4 price, +8 size   (R)
//
// Single outstanding transaction (matches the latency_counter AXI-Lite style and
// the F1 OCL bus). Bridge owns the OCL slave; forwards the low range to the core
// s_axil as an AXI-Lite master.

module cl_tick_to_trade_core #(
    parameter int OCL_AW   = 32,
    parameter int DEC_CAP  = 128,   // captured decisions
    parameter int IFIFO_AW = 6      // ingress FIFO depth = 64
)(
    input  logic        clk,
    input  logic        rst_n,

    input  logic        halt,
    output logic        risk_reject,
    output logic [2:0]  risk_reason,

    // OCL AXI-Lite slave (from Shell)
    input  logic [OCL_AW-1:0] ocl_awaddr,
    input  logic              ocl_awvalid,
    output logic              ocl_awready,
    input  logic [31:0]       ocl_wdata,
    input  logic [3:0]        ocl_wstrb,
    input  logic              ocl_wvalid,
    output logic              ocl_wready,
    output logic [1:0]        ocl_bresp,
    output logic              ocl_bvalid,
    input  logic              ocl_bready,
    input  logic [OCL_AW-1:0] ocl_araddr,
    input  logic              ocl_arvalid,
    output logic              ocl_arready,
    output logic [31:0]       ocl_rdata,
    output logic [1:0]        ocl_rresp,
    output logic              ocl_rvalid,
    input  logic              ocl_rready
);

    // ───────────────────────── core pipeline I/O ────────────────────────────
    logic [7:0]  s_axis_tdata;  logic s_axis_tvalid, s_axis_tready, s_axis_tlast;
    logic [71:0] m_axis_tdata;  logic m_axis_tvalid, m_axis_tready;

    // core latency-counter AXI-Lite (driven by the bridge as master)
    logic [8:0]  c_awaddr;  logic c_awvalid, c_awready;
    logic [31:0] c_wdata;   logic [3:0] c_wstrb; logic c_wvalid, c_wready;
    logic [1:0]  c_bresp;   logic c_bvalid, c_bready;
    logic [8:0]  c_araddr;  logic c_arvalid, c_arready;
    logic [31:0] c_rdata;   logic [1:0] c_rresp; logic c_rvalid, c_rready;

    tick_to_trade_top u_core (
        .clk(clk), .rst_n(rst_n), .halt(halt),
        .risk_reject(risk_reject), .risk_reason(risk_reason),
        .s_axis_tdata(s_axis_tdata), .s_axis_tvalid(s_axis_tvalid),
        .s_axis_tready(s_axis_tready), .s_axis_tlast(s_axis_tlast),
        .m_axis_tdata(m_axis_tdata), .m_axis_tvalid(m_axis_tvalid),
        .m_axis_tready(m_axis_tready),
        .s_axil_awaddr(c_awaddr), .s_axil_awvalid(c_awvalid), .s_axil_awready(c_awready),
        .s_axil_wdata(c_wdata), .s_axil_wstrb(c_wstrb), .s_axil_wvalid(c_wvalid),
        .s_axil_wready(c_wready), .s_axil_bresp(c_bresp), .s_axil_bvalid(c_bvalid),
        .s_axil_bready(c_bready), .s_axil_araddr(c_araddr), .s_axil_arvalid(c_arvalid),
        .s_axil_arready(c_arready), .s_axil_rdata(c_rdata), .s_axil_rresp(c_rresp),
        .s_axil_rvalid(c_rvalid), .s_axil_rready(c_rready)
    );

    // ───────────────────────── ingress FIFO -> s_axis ───────────────────────
    localparam int IF_DEPTH = (1 << IFIFO_AW);
    logic [8:0]          ififo [0:IF_DEPTH-1];   // {tlast, byte}
    logic [IFIFO_AW:0]   if_cnt;
    logic [IFIFO_AW-1:0] if_rd, if_wr;
    wire if_full  = (if_cnt == IF_DEPTH[IFIFO_AW:0]);
    wire if_empty = (if_cnt == '0);
    logic if_push; logic [8:0] if_pushval;

    assign s_axis_tvalid = !if_empty;
    assign s_axis_tdata  = ififo[if_rd][7:0];
    assign s_axis_tlast  = ififo[if_rd][8];
    wire   if_pop = s_axis_tvalid && s_axis_tready;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            if_rd <= '0; if_wr <= '0; if_cnt <= '0;
        end else begin
            if (if_push && !if_full) begin
                ififo[if_wr] <= if_pushval; if_wr <= if_wr + 1'b1;
            end
            if (if_pop) if_rd <= if_rd + 1'b1;
            case ({if_push && !if_full, if_pop})
                2'b10: if_cnt <= if_cnt + 1'b1;
                2'b01: if_cnt <= if_cnt - 1'b1;
                default: ;
            endcase
        end
    end

    // ───────────────────────── egress capture <- m_axis ─────────────────────
    localparam int DEC_AW = $clog2(DEC_CAP);
    logic        dec_action [0:DEC_CAP-1];
    logic [31:0] dec_price  [0:DEC_CAP-1];
    logic [31:0] dec_size   [0:DEC_CAP-1];
    logic [DEC_AW:0] dec_cnt;
    logic        dec_overflow;

    assign m_axis_tready = 1'b1;   // host-side capture never back-pressures

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            dec_cnt <= '0; dec_overflow <= 1'b0;
        end else if (m_axis_tvalid) begin
            if (dec_cnt < DEC_CAP[DEC_AW:0]) begin
                dec_action[dec_cnt[DEC_AW-1:0]] <= m_axis_tdata[64];
                dec_price [dec_cnt[DEC_AW-1:0]] <= m_axis_tdata[63:32];
                dec_size  [dec_cnt[DEC_AW-1:0]] <= m_axis_tdata[31:0];
                dec_cnt <= dec_cnt + 1'b1;
            end else
                dec_overflow <= 1'b1;
        end
    end

    // ───────────────────────── OCL AXI-Lite slave (+ core forward) ───────────
    localparam logic [1:0] OKAY = 2'b00;
    assign ocl_bresp = OKAY;
    assign ocl_rresp = OKAY;
    assign c_wstrb   = 4'hF;

    function automatic logic is_low(input logic [OCL_AW-1:0] a);
        return (a[OCL_AW-1:9] == '0);   // a < 0x200
    endfunction

    // ---- write path ----
    typedef enum logic [1:0] { W_IDLE, W_FWD, W_RESP } wstate_t;
    wstate_t wst;
    logic [OCL_AW-1:0] waddr_l; logic [31:0] wdata_l;

    assign ocl_awready = (wst == W_IDLE);
    assign ocl_wready  = (wst == W_IDLE);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wst <= W_IDLE; ocl_bvalid <= 1'b0;
            c_awvalid <= 1'b0; c_wvalid <= 1'b0; c_bready <= 1'b0;
            c_awaddr <= '0; c_wdata <= '0;
            if_push <= 1'b0; if_pushval <= '0;
        end else begin
            if_push <= 1'b0;
            case (wst)
                W_IDLE: begin
                    if (ocl_awvalid && ocl_wvalid) begin
                        waddr_l <= ocl_awaddr; wdata_l <= ocl_wdata;
                        if (is_low(ocl_awaddr)) begin
                            c_awaddr  <= ocl_awaddr[8:0]; c_wdata <= ocl_wdata;
                            c_awvalid <= 1'b1; c_wvalid <= 1'b1; c_bready <= 1'b1;
                            wst <= W_FWD;
                        end else begin
                            if (ocl_awaddr[8:0] == 9'h000) begin   // 0x200 ingress
                                if_push    <= 1'b1;
                                if_pushval <= {ocl_wdata[8], ocl_wdata[7:0]};
                            end
                            ocl_bvalid <= 1'b1; wst <= W_RESP;
                        end
                    end
                end
                W_FWD: begin
                    if (c_awvalid && c_awready) c_awvalid <= 1'b0;
                    if (c_wvalid  && c_wready ) c_wvalid  <= 1'b0;
                    if (c_bvalid) begin
                        c_bready <= 1'b0; ocl_bvalid <= 1'b1; wst <= W_RESP;
                    end
                end
                W_RESP: if (ocl_bvalid && ocl_bready) begin
                    ocl_bvalid <= 1'b0; wst <= W_IDLE;
                end
            endcase
        end
    end

    // ---- read path ----
    typedef enum logic [1:0] { R_IDLE, R_FWD, R_RESP } rstate_t;
    rstate_t rst_r;
    logic [OCL_AW-1:0] raddr_l;

    assign ocl_arready = (rst_r == R_IDLE);

    // Decision egress region: base 0x400, 16 bytes/decision.
    localparam logic [OCL_AW-1:0] DEC_BASE = 'h400;
    wire [OCL_AW-1:0] dec_off  = ocl_araddr - DEC_BASE;
    wire              dec_region = (ocl_araddr >= DEC_BASE) &&
                                   (ocl_araddr < DEC_BASE + 16*DEC_CAP);
    wire [DEC_AW-1:0] dec_idx  = dec_off[DEC_AW-1+4:4];

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rst_r <= R_IDLE; ocl_rvalid <= 1'b0; ocl_rdata <= '0;
            c_arvalid <= 1'b0; c_araddr <= '0; c_rready <= 1'b0;
        end else begin
            case (rst_r)
                R_IDLE: if (ocl_arvalid) begin
                    raddr_l <= ocl_araddr;
                    if (is_low(ocl_araddr)) begin
                        c_araddr <= ocl_araddr[8:0]; c_arvalid <= 1'b1;
                        c_rready <= 1'b1; rst_r <= R_FWD;
                    end else begin
                        // local registers
                        if (ocl_araddr[11:0] == 12'h204)
                            ocl_rdata <= {16'b0, dec_cnt[7:0],
                                          5'b0, dec_overflow, if_empty, if_full};
                        else if (ocl_araddr[11:0] == 12'h2FC)
                            ocl_rdata <= {{(32-DEC_AW-1){1'b0}}, dec_cnt};
                        else if (dec_region) begin
                            unique case (ocl_araddr[3:2])
                                2'd0: ocl_rdata <= {31'b0, dec_action[dec_idx]};
                                2'd1: ocl_rdata <= dec_price[dec_idx];
                                2'd2: ocl_rdata <= dec_size [dec_idx];
                                default: ocl_rdata <= 32'hDEAD_BEEF;
                            endcase
                        end else
                            ocl_rdata <= 32'hDEAD_BEEF;
                        ocl_rvalid <= 1'b1; rst_r <= R_RESP;
                    end
                end
                R_FWD: begin
                    if (c_arvalid && c_arready) c_arvalid <= 1'b0;
                    if (c_rvalid) begin
                        ocl_rdata <= c_rdata; c_rready <= 1'b0;
                        ocl_rvalid <= 1'b1; rst_r <= R_RESP;
                    end
                end
                R_RESP: if (ocl_rvalid && ocl_rready) begin
                    ocl_rvalid <= 1'b0; rst_r <= R_IDLE;
                end
            endcase
        end
    end

endmodule
