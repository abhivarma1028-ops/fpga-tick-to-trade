// Hardware Market Maker — two-sided quoting in RTL (timing-closed)
// ---------------------------------------------------------------------------
// Given top-of-book (best bid/ask price + size) and our current inventory,
// compute a two-sided quote to POST (earn the spread), inventory-skewed toward
// flat. Hardware counterpart of host/market_maker.py.
//
//   fair (microprice) = (bid_px*ask_sz + ask_px*bid_sz) / (bid_sz + ask_sz)
//   centre            = fair - inventory * SKEW
//   bid_price         = centre - HALF_SPREAD ;  ask_price = centre + HALF_SPREAD
//
// The microprice needs a DIVISION (a MM produces an actual price, so it can't
// cross-multiply the divide away like strategy_imbalance does). A single-cycle
// divide is ~W carry chains deep and fails 200 MHz (measured WNS -39 ns). Here
// the divide is done by a PIPELINED divider (pipe_divu, W stages, one compare-
// subtract each) so every stage is short and timing closes; cost is DIV_W cycles
// of latency (throughput stays 1 quote/cycle). Inventory rides the divider
// pipeline as payload so it lines up with the quotient.
//
// Latency: 1 (input reg) + DIV_W (divider) + 1 (output reg) cycles.
// ---------------------------------------------------------------------------
module strategy_market_maker #(
    parameter int HALF_SPREAD   = 200,   // ticks each side from centre (200 = $0.02)
    parameter int SKEW          = 2,     // ticks of skew per share of inventory
    parameter int QUOTE_SIZE    = 100,   // shares per side
    parameter int MAX_POSITION  = 1000,  // suppress the side that would breach this
    parameter int DIV_W         = 64     // divider width (>= numerator width)
)(
    input  logic               clk,
    input  logic               rst_n,

    input  logic               book_valid,
    input  logic [31:0]        best_bid_price,
    input  logic [31:0]        best_ask_price,
    input  logic [31:0]        bid_size,
    input  logic [31:0]        ask_size,
    input  logic signed [31:0] inventory,     // + long, - short

    output logic               quote_valid,   // one-cycle pulse when a quote is produced
    output logic [31:0]        bid_price,      // 0 = do not quote this side
    output logic [31:0]        bid_qty,
    output logic [31:0]        ask_price,      // 0 = do not quote this side
    output logic [31:0]        ask_qty
);
    // -----------------------------------------------------------------------
    // Stage 1 (registered): numerator, denominator, normal-book guard, inventory
    // -----------------------------------------------------------------------
    logic [63:0]        num_r;
    logic [63:0]        den_r;
    logic               s1_valid;
    logic signed [31:0] inv_r;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            num_r <= '0; den_r <= '0; s1_valid <= 1'b0; inv_r <= '0;
        end else begin
            num_r    <= 64'(best_bid_price) * 64'(ask_size)
                      + 64'(best_ask_price) * 64'(bid_size);
            den_r    <= 64'(bid_size) + 64'(ask_size);
            s1_valid <= book_valid && (best_ask_price > best_bid_price)
                        && ((bid_size + ask_size) != 0);
            inv_r    <= inventory;
        end
    end

    // -----------------------------------------------------------------------
    // Pipelined microprice divide; inventory rides along as payload
    // -----------------------------------------------------------------------
    logic         d_valid;
    logic [63:0]  micro_d;
    logic [31:0]  inv_d_u;

    pipe_divu #(.W(64), .PW(32)) u_div (
        .clk(clk), .rst_n(rst_n),
        .in_valid(s1_valid), .num(num_r), .den(den_r), .payload(inv_r),
        .out_valid(d_valid), .quo(micro_d), .payload_o(inv_d_u)
    );

    // -----------------------------------------------------------------------
    // Stage 2 (combinational from divider outputs) + registered quote
    // -----------------------------------------------------------------------
    localparam logic signed [63:0] HS = 64'(HALF_SPREAD);
    logic signed [31:0] inv_d;
    logic signed [63:0] centre_c, bid_c, ask_c;
    logic [31:0]        bidq_c, askq_c, bidp_c, askp_c;

    always_comb begin
        inv_d    = $signed(inv_d_u);
        centre_c = $signed(micro_d) - 64'(inv_d) * 64'(SKEW);
        bid_c    = centre_c - HS;
        ask_c    = centre_c + HS;
        if (ask_c <= bid_c) ask_c = bid_c + 64'sd1;

        bidq_c = QUOTE_SIZE; askq_c = QUOTE_SIZE;
        if (inv_d + QUOTE_SIZE > MAX_POSITION)  bidq_c = 32'd0;
        if (inv_d - QUOTE_SIZE < -MAX_POSITION) askq_c = 32'd0;

        bidp_c = (bid_c < 0) ? 32'd0 : bid_c[31:0];
        askp_c = (ask_c < 0) ? 32'd0 : ask_c[31:0];
        if (bidq_c == 0) bidp_c = 32'd0;
        if (askq_c == 0) askp_c = 32'd0;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            quote_valid <= 1'b0;
            bid_price <= '0; bid_qty <= '0; ask_price <= '0; ask_qty <= '0;
        end else begin
            quote_valid <= d_valid;
            if (d_valid) begin
                bid_price <= bidp_c;
                bid_qty   <= bidq_c;
                ask_price <= askp_c;
                ask_qty   <= askq_c;
            end
        end
    end

endmodule
