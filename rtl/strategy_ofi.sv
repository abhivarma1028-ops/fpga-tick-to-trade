// Order Flow Imbalance (OFI) strategy — Cont, Kukanov & Stoikov (2014)
// ---------------------------------------------------------------------------
// The best-documented short-horizon predictor: mid-price change is driven,
// near-linearly, by order-flow imbalance at the touch. Per L1 update:
//   e_b = q_b*1{P_b>=P_b'} - q_b'*1{P_b<=P_b'}
//   e_a = q_a*1{P_a<=P_a'} - q_a'*1{P_a>=P_a'}
//   OFI increment = e_b - e_a
// A WINDOW-tap running sum of increments is thresholded to BUY/SELL. Being purely
// add/subtract/compare (NO DIVISION) this closes 200 MHz trivially — the opposite
// of the market maker's divide-bound path.
//
// 2-stage pipeline: (1) compute+register the increment & book snapshot; (2) update
// the windowed sum and register the decision.
// ---------------------------------------------------------------------------
module strategy_ofi #(
    parameter int WINDOW     = 8,      // taps summed
    parameter int THRESHOLD  = 1500,   // |windowed OFI| to fire
    parameter int BASE_LOT   = 100,
    parameter int MAX_LOT    = 300
)(
    input  logic        clk,
    input  logic        rst_n,

    input  logic        book_valid,
    input  logic [31:0] best_bid_price,
    input  logic [31:0] best_ask_price,
    input  logic [31:0] bid_size,
    input  logic [31:0] ask_size,

    output logic        decision_valid,   // one-cycle pulse
    output logic        action,           // 0=BUY 1=SELL
    output logic [31:0] order_price,       // ask for BUY, bid for SELL
    output logic [31:0] order_size
);
    // previous top-of-book
    logic [31:0] pbp, pbs, pap, pas;
    logic        have_prev;

    // ---- Stage 1 (combinational): OFI increment vs previous quote ----
    logic signed [33:0] e_b, e_a, inc_c;
    logic               normal_c;
    always_comb begin
        normal_c = book_valid && (best_ask_price > best_bid_price) && (best_bid_price != 0);
        e_b = (best_bid_price >= pbp ? $signed({2'b0, bid_size}) : 34'sd0)
            - (best_bid_price <= pbp ? $signed({2'b0, pbs})       : 34'sd0);
        e_a = (best_ask_price <= pap ? $signed({2'b0, ask_size}) : 34'sd0)
            - (best_ask_price >= pap ? $signed({2'b0, pas})       : 34'sd0);
        inc_c = e_b - e_a;
    end

    // ---- Stage 1 registers ----
    logic signed [33:0] inc_r;
    logic [31:0]        bidp_r, askp_r;
    logic               s1_valid;         // increment valid (had a prev quote)

    // window shift register of increments + running sum
    logic signed [33:0] taps [0:WINDOW-1];
    logic signed [47:0] ofi_sum;          // wide enough for WINDOW*inc

    // The next windowed sum: old sum, minus the tap leaving the window, plus the
    // newest increment. Computed ONCE here rather than four times inline.
    //
    // taps[] and inc_r are 34-bit signed while ofi_sum is 48-bit, so the operands
    // are widened explicitly. The implicit extension was already correct (both
    // are signed, so it sign-extends), but saying so removes 12 Verilator
    // WIDTHEXPAND warnings that forced the whole regression to run -Wno-fatal --
    // which in turn hid any genuine width bug behind the noise.
    logic signed [47:0] ofi_next;
    assign ofi_next = ofi_sum - 48'(taps[0]) + 48'(inc_r);

    // Sign-extended threshold, so the comparisons below are 48-bit on both sides.
    localparam logic signed [47:0] THRESH_W = 48'(THRESHOLD);

    integer k;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pbp<=0; pbs<=0; pap<=0; pas<=0; have_prev<=0;
            inc_r<=0; bidp_r<=0; askp_r<=0; s1_valid<=0;
            ofi_sum<=0;
            for (k=0;k<WINDOW;k=k+1) taps[k]<=0;
            decision_valid<=0; action<=0; order_price<=0; order_size<=0;
        end else begin
            // ---- Stage 1: capture increment + snapshot ----
            if (!normal_c) begin
                have_prev <= 1'b0;         // reset the reference on a bad book
                s1_valid  <= 1'b0;
            end else begin
                pbp<=best_bid_price; pbs<=bid_size; pap<=best_ask_price; pas<=ask_size;
                bidp_r<=best_bid_price; askp_r<=best_ask_price;
                inc_r <= inc_c;
                s1_valid <= have_prev;     // only valid once we have a previous quote
                have_prev <= 1'b1;
            end

            // ---- Stage 2: window sum + decision ----
            decision_valid <= 1'b0;
            if (s1_valid) begin
                // slide the window: new sum = old - oldest + newest
                ofi_sum <= ofi_next;
                for (k=0;k<WINDOW-1;k=k+1) taps[k]<=taps[k+1];
                taps[WINDOW-1]<=inc_r;

                // threshold the (about-to-be) updated sum
                if (ofi_next >= THRESH_W) begin
                    decision_valid<=1'b1; action<=1'b0; order_price<=askp_r;
                    order_size <= lot(ofi_next);
                end else if (ofi_next <= -THRESH_W) begin
                    decision_valid<=1'b1; action<=1'b1; order_price<=bidp_r;
                    order_size <= lot(-ofi_next);
                end
            end
        end
    end

    // size tiers by OFI strength — COMPARE-based (no divide; a wide constant
    // divide by THRESHOLD was the 200 MHz critical path). 1x/2x/3x*BASE_LOT,
    // capped at MAX_LOT. Identical result to base*floor(|ofi|/thr) for these params.
    function automatic logic [31:0] lot(input logic signed [47:0] mag);
        logic [31:0] sz;
        begin
            if      (mag >= 3*THRESHOLD) sz = 3*BASE_LOT;
            else if (mag >= 2*THRESHOLD) sz = 2*BASE_LOT;
            else                         sz = BASE_LOT;
            lot = (sz > MAX_LOT) ? MAX_LOT : sz;
        end
    endfunction

endmodule
