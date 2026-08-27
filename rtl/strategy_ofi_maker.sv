// OFI-driven Market Maker — "predict + quote" in RTL (timing-closed)
// ---------------------------------------------------------------------------
// The hardware market maker, but the quote CENTRE is tilted by the OFI-predicted
// drift (Cont-Kukanov-Stoikov) so it leans into the move it expects:
//
//   fair (microprice) = (bid_px*ask_sz + ask_px*bid_sz) / (bid_sz + ask_sz)
//   tilt              = clamp(OFI_window >>> ALPHA_SHIFT, +/-MAX_TILT)
//   centre            = fair - inventory*SKEW + tilt
//   bid/ask           = centre -/+ HALF_SPREAD
//
// Reuses the pipelined divider (pipe_divu) for the microprice — a MM must divide,
// and a single-cycle divide fails 200 MHz. The OFI windowed sum + tilt are formed
// in stage 1 and ride the divider pipeline as PAYLOAD (packed with inventory),
// so they line up with the quotient. ALPHA_SHIFT (alpha_den = 2**shift) keeps the
// tilt a shift, not a second divide.
//
// Latency: 1 (input reg) + DIV_W (divider) + 1 (output reg) cycles.
// ---------------------------------------------------------------------------
module strategy_ofi_maker #(
    parameter int HALF_SPREAD   = 200,
    parameter int SKEW          = 2,
    parameter int QUOTE_SIZE    = 100,
    parameter int MAX_POSITION  = 1000,
    parameter int OFI_WINDOW    = 8,
    parameter int ALPHA_SHIFT   = 2,     // tilt = ofi >>> ALPHA_SHIFT
    parameter int MAX_TILT      = 150,   // clamp; keep < HALF_SPREAD
    parameter int DIV_W         = 64
)(
    input  logic               clk,
    input  logic               rst_n,

    input  logic               book_valid,
    input  logic [31:0]        best_bid_price,
    input  logic [31:0]        best_ask_price,
    input  logic [31:0]        bid_size,
    input  logic [31:0]        ask_size,
    input  logic signed [31:0] inventory,

    output logic               quote_valid,
    output logic [31:0]        bid_price,
    output logic [31:0]        bid_qty,
    output logic [31:0]        ask_price,
    output logic [31:0]        ask_qty
);
    // -----------------------------------------------------------------------
    // OFI windowed sum (order-flow prediction) — previous top-of-book + taps
    // -----------------------------------------------------------------------
    logic [31:0] pbp, pbs, pap, pas;
    logic        have_prev;
    logic signed [33:0] taps [0:OFI_WINDOW-1];
    logic signed [47:0] ofi_sum;

    // combinational OFI increment vs previous quote
    logic signed [33:0] e_b, e_a, inc_c;
    logic signed [47:0] new_sum;
    logic signed [47:0] tilt_full;
    logic signed [31:0] tilt_c;
    always_comb begin
        e_b = (best_bid_price >= pbp ? $signed({2'b0, bid_size}) : 34'sd0)
            - (best_bid_price <= pbp ? $signed({2'b0, pbs})       : 34'sd0);
        e_a = (best_ask_price <= pap ? $signed({2'b0, ask_size}) : 34'sd0)
            - (best_ask_price >= pap ? $signed({2'b0, pas})       : 34'sd0);
        inc_c   = e_b - e_a;
        // on the first tick after a (re)start there is no valid increment
        new_sum = have_prev ? (ofi_sum - taps[0] + inc_c) : ofi_sum;
        tilt_full = new_sum >>> ALPHA_SHIFT;
        if      (tilt_full >  MAX_TILT) tilt_c = MAX_TILT;
        else if (tilt_full < -MAX_TILT) tilt_c = -MAX_TILT;
        else                            tilt_c = tilt_full[31:0];
    end

    // -----------------------------------------------------------------------
    // Stage 1 (registered): microprice num/den, guard, payload {tilt, inv}
    // -----------------------------------------------------------------------
    logic [63:0] num_r, den_r;
    logic        s1_valid;
    logic [63:0] pl_r;                    // {tilt[31:0], inv[31:0]}
    logic        normal_c;
    integer k;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pbp<=0; pbs<=0; pap<=0; pas<=0; have_prev<=0; ofi_sum<=0;
            for (k=0;k<OFI_WINDOW;k=k+1) taps[k]<=0;
            num_r<=0; den_r<=0; s1_valid<=0; pl_r<=0;
        end else begin
            normal_c = book_valid && (best_ask_price > best_bid_price)
                       && ((bid_size + ask_size) != 0);
            if (!normal_c) begin
                have_prev <= 1'b0;        // matches golden: prev=None on bad book
                s1_valid  <= 1'b0;
            end else begin
                // advance the OFI window (only shift once we have a previous quote)
                if (have_prev) begin
                    ofi_sum <= new_sum;
                    for (k=0;k<OFI_WINDOW-1;k=k+1) taps[k]<=taps[k+1];
                    taps[OFI_WINDOW-1]<=inc_c;
                end
                pbp<=best_bid_price; pbs<=bid_size; pap<=best_ask_price; pas<=ask_size;
                have_prev<=1'b1;

                num_r <= 64'(best_bid_price)*64'(ask_size)
                       + 64'(best_ask_price)*64'(bid_size);
                den_r <= 64'(bid_size) + 64'(ask_size);
                pl_r  <= {tilt_c, inventory};
                s1_valid <= 1'b1;
            end
        end
    end

    // -----------------------------------------------------------------------
    // Pipelined microprice divide; {tilt,inv} ride as payload
    // -----------------------------------------------------------------------
    logic        d_valid;
    logic [63:0] micro_d;
    logic [63:0] pl_d;

    pipe_divu #(.W(64), .PW(64)) u_div (
        .clk(clk), .rst_n(rst_n),
        .in_valid(s1_valid), .num(num_r), .den(den_r), .payload(pl_r),
        .out_valid(d_valid), .quo(micro_d), .payload_o(pl_d)
    );

    // -----------------------------------------------------------------------
    // Stage 2: centre = micro - inv*SKEW + tilt, then quote
    // -----------------------------------------------------------------------
    localparam logic signed [63:0] HS = 64'(HALF_SPREAD);
    logic signed [31:0] inv_d, tilt_d;
    logic signed [63:0] centre_c, bid_c, ask_c;
    logic [31:0]        bidq_c, askq_c, bidp_c, askp_c;

    always_comb begin
        inv_d    = $signed(pl_d[31:0]);
        tilt_d   = $signed(pl_d[63:32]);
        centre_c = $signed(micro_d) - 64'(inv_d)*64'(SKEW) + 64'(tilt_d);
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
            quote_valid<=1'b0; bid_price<=0; bid_qty<=0; ask_price<=0; ask_qty<=0;
        end else begin
            quote_valid <= d_valid;
            if (d_valid) begin
                bid_price<=bidp_c; bid_qty<=bidq_c; ask_price<=askp_c; ask_qty<=askq_c;
            end
        end
    end

endmodule
