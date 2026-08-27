// Market-Maker Accelerator — top level
// ---------------------------------------------------------------------------
// A self-contained, synthesizable accelerator that a host (PS / EC2 / F2 shell)
// drives over a single AXI-Lite CSR bus:
//   * write the current top-of-book + our inventory to control registers,
//   * strobe GO,
//   * the hardware market maker (strategy_market_maker + pipe_divu) computes a
//     two-sided quote; latency_counter measures the GO->quote compute time,
//   * read the quote (bid/ask price+qty) and latency back.
//
// AXI-Lite map (word addresses):
//   0x000-0x1FF : latency_counter (histogram + last_latency + clear @0x104)
//   0x200 W     : best_bid_price       0x214 W : GO strobe (any write pulses)
//   0x204 W     : best_ask_price       0x220 R : status {quote_valid}
//   0x208 W     : bid_size             0x224 R : bid_price
//   0x20C W     : ask_size             0x228 R : bid_qty
//   0x210 W     : inventory (signed)   0x22C R : ask_price
//                                      0x230 R : ask_qty
// The MM itself is unchanged (timing-closed at +3.0 ns); this only adds the CSR
// shell + latency instrumentation so it can live in an AFI.
// ---------------------------------------------------------------------------
module market_maker_top #(
    parameter int HALF_SPREAD  = 200,
    parameter int SKEW         = 2,
    parameter int QUOTE_SIZE   = 100,
    parameter int MAX_POSITION = 1000,
    parameter int AW           = 12          // 0x000-0xFFF
)(
    input  logic          clk,
    input  logic          rst_n,

    // AXI-Lite slave
    input  logic [AW-1:0] s_awaddr,
    input  logic          s_awvalid,
    output logic          s_awready,
    input  logic [31:0]   s_wdata,
    input  logic [3:0]    s_wstrb,
    input  logic          s_wvalid,
    output logic          s_wready,
    output logic [1:0]    s_bresp,
    output logic          s_bvalid,
    input  logic          s_bready,
    input  logic [AW-1:0] s_araddr,
    input  logic          s_arvalid,
    output logic          s_arready,
    output logic [31:0]   s_rdata,
    output logic [1:0]    s_rresp,
    output logic          s_rvalid,
    input  logic          s_rready
);
    localparam [1:0] OKAY = 2'b00;

    // ---- control registers (host-written) ----
    logic [31:0]        r_bid_px, r_ask_px, r_bid_sz, r_ask_sz;
    logic signed [31:0] r_inv;
    logic               go;                    // one-cycle strobe

    // ---- MM outputs (latched for host read) ----
    logic        q_valid;
    logic [31:0] q_bidp, q_bidq, q_askp, q_askq;

    // ===================================================================
    // Write channel (simple 1-outstanding handshake)
    // ===================================================================
    logic aw_seen, w_seen;
    logic [AW-1:0] awaddr_q;
    logic [31:0]   wdata_q;

    assign s_awready = !aw_seen;
    assign s_wready  = !w_seen;

    wire wr_fire = (aw_seen || s_awvalid) && (w_seen || s_wvalid);
    wire [AW-1:0] wr_addr = aw_seen ? awaddr_q : s_awaddr;
    wire [31:0]   wr_data = w_seen  ? wdata_q  : s_wdata;

    // latency_counter clear strobe (write to 0x104)
    logic lc_clear;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            aw_seen<=0; w_seen<=0; awaddr_q<=0; wdata_q<=0;
            s_bvalid<=0; go<=0; lc_clear<=0;
            r_bid_px<=0; r_ask_px<=0; r_bid_sz<=0; r_ask_sz<=0; r_inv<=0;
        end else begin
            go<=0; lc_clear<=0;                  // default: strobes low
            if (s_awvalid && s_awready) begin aw_seen<=1; awaddr_q<=s_awaddr; end
            if (s_wvalid  && s_wready ) begin w_seen<=1;  wdata_q<=s_wdata;  end
            if (wr_fire && !s_bvalid) begin
                case (wr_addr)
                    12'h200: r_bid_px <= wr_data;
                    12'h204: r_ask_px <= wr_data;
                    12'h208: r_bid_sz <= wr_data;
                    12'h20C: r_ask_sz <= wr_data;
                    12'h210: r_inv    <= wr_data;
                    12'h214: go       <= 1'b1;    // GO
                    12'h104: lc_clear <= 1'b1;    // latency clear
                    default: ;
                endcase
                aw_seen<=0; w_seen<=0; s_bvalid<=1;
            end
            if (s_bvalid && s_bready) s_bvalid<=0;
        end
    end
    assign s_bresp = OKAY;

    // ===================================================================
    // latency_counter — measures GO(msg_start) -> quote_valid(decision)
    // ===================================================================
    logic [31:0] lc_rdata;
    latency_counter #(.AXIL_ADDR_W(9)) u_lat (
        .clk(clk), .rst_n(rst_n),
        .msg_start(go), .decision_valid(q_valid),
        // drive its clear via our decode (bit0 of a write to 0x104)
        .s_axil_awaddr(9'h104), .s_axil_awvalid(lc_clear), .s_axil_awready(),
        // wstrb was previously left unconnected (Verilator PINMISSING). Harmless
        // in practice -- latency_counter's clear decode looks only at wdata[0] --
        // but an unconnected input is an invitation to a real bug later. This is
        // a full 32-bit write, so all four byte lanes are enabled.
        .s_axil_wdata(32'h1), .s_axil_wstrb(4'hF),
        .s_axil_wvalid(lc_clear), .s_axil_wready(),
        .s_axil_bresp(), .s_axil_bvalid(), .s_axil_bready(1'b1),
        .s_axil_araddr(s_araddr[8:0]), .s_axil_arvalid(lc_arvalid),
        .s_axil_arready(lc_arready),
        .s_axil_rdata(lc_rdata), .s_axil_rresp(), .s_axil_rvalid(lc_rvalid),
        .s_axil_rready(s_rready)
    );

    // ===================================================================
    // The hardware market maker
    // ===================================================================
    strategy_market_maker #(
        .HALF_SPREAD(HALF_SPREAD), .SKEW(SKEW),
        .QUOTE_SIZE(QUOTE_SIZE), .MAX_POSITION(MAX_POSITION)
    ) u_mm (
        .clk(clk), .rst_n(rst_n),
        .book_valid(go),
        .best_bid_price(r_bid_px), .best_ask_price(r_ask_px),
        .bid_size(r_bid_sz), .ask_size(r_ask_sz), .inventory(r_inv),
        .quote_valid(q_valid),
        .bid_price(q_bidp), .bid_qty(q_bidq),
        .ask_price(q_askp), .ask_qty(q_askq)
    );

    // latch the last quote for host reads
    logic        last_valid;
    logic [31:0] last_bidp, last_bidq, last_askp, last_askq;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            last_valid<=0; last_bidp<=0; last_bidq<=0; last_askp<=0; last_askq<=0;
        end else if (q_valid) begin
            last_valid<=1; last_bidp<=q_bidp; last_bidq<=q_bidq;
            last_askp<=q_askp; last_askq<=q_askq;
        end
    end

    // ===================================================================
    // Read channel — 0x000-0x1FF from latency_counter, 0x200+ our status
    // ===================================================================
    wire is_lat = (s_araddr < 12'h200);
    logic lc_arvalid, lc_arready, lc_rvalid;
    assign lc_arvalid = s_arvalid && is_lat;

    logic        mm_rvalid;
    logic [31:0] mm_rdata;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin mm_rvalid<=0; mm_rdata<=0; end
        else begin
            if (s_arvalid && !is_lat && !mm_rvalid) begin
                mm_rvalid <= 1;
                case (s_araddr)
                    12'h220: mm_rdata <= {31'd0, last_valid};
                    12'h224: mm_rdata <= last_bidp;
                    12'h228: mm_rdata <= last_bidq;
                    12'h22C: mm_rdata <= last_askp;
                    12'h230: mm_rdata <= last_askq;
                    default: mm_rdata <= 32'hDEAD_BEEF;
                endcase
            end else if (mm_rvalid && s_rready) mm_rvalid<=0;
        end
    end

    assign s_arready = is_lat ? lc_arready : (!mm_rvalid);
    assign s_rvalid  = is_lat ? lc_rvalid  : mm_rvalid;
    assign s_rdata   = is_lat ? lc_rdata   : mm_rdata;
    assign s_rresp   = OKAY;

endmodule
