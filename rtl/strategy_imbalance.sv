// Book-Imbalance Strategy — Milestone 2 (depth-weighted, 2-stage pipeline)
// Signal: buy when weighted bid volume >> weighted ask volume, sell when the
// reverse. "Weighted" sums the top NLEVELS of book depth, weighting the touch
// (level 0) most heavily (weight N) down to the deepest tracked level (weight 1).
//
// Avoids division (expensive in RTL) via cross-multiply on the weighted volumes:
//   buy  signal: BID_THRESH * w_ask < w_bid * 10
//   sell signal: ASK_THRESH * w_bid < w_ask * 10
// All fixed-point integer arithmetic; no floats anywhere.
//
// TIMING (B3 correction): the weighted-volume summation and the cross-multiply
// were one long combinational cone (book reg → Σ(weight·size) → 64-bit
// cross-multiply → compare → decision reg) that, together with the downstream
// risk collar, formed the 200 MHz critical path. It is now split into two
// pipeline stages:
//   Stage 1 (registered): weighted volumes w_bid/w_ask + book snapshot + the
//            spread/eligibility guard.
//   Stage 2 (registered): cross-multiply + lot sizing + the fire decision.
// This costs +1 cycle of tick-to-trade latency (book→decision is now 2 cycles).
//
// Risk guards:
//   * SPREAD GUARD  — only trade a normal book (ask > bid) within MAX_SPREAD_TICKS.
//   * COOLDOWN      — suppress new decisions for COOLDOWN_CYCLES after one fires.
//   * LOT SIZING    — order size scales with imbalance strength, capped at MAX_LOT.

module strategy_imbalance #(
    parameter int NLEVELS          = 4,     // book depth consumed (matches order book)
    parameter int BID_THRESH       = 15,    // w_bid/w_ask ratio * 10 to trigger BUY
    parameter int ASK_THRESH       = 15,    // w_ask/w_bid ratio * 10 to trigger SELL
    parameter int MAX_SPREAD_TICKS = 1000,  // max spread in ticks (/10000). 1000 = $0.10
    parameter int COOLDOWN_CYCLES  = 8,     // suppress new decisions for N cycles after a fire
    parameter int BASE_LOT         = 100,
    parameter int MAX_LOT          = 250
)(
    input  logic        clk,
    input  logic        rst_n,

    // Book state input (from order_book_m2)
    input  logic        book_valid,
    input  logic [31:0] best_bid_price,         // level 0 (for pricing/spread)
    input  logic [31:0] best_ask_price,         // level 0 (for pricing/spread)
    input  logic [NLEVELS*32-1:0] bid_level_size,   // {level N-1 .. 0} sizes
    input  logic [NLEVELS*32-1:0] ask_level_size,

    // Decision output — one-cycle pulse
    output logic        decision_valid,
    output logic        action,         // 0=BUY 1=SELL
    output logic [31:0] order_price,    // aggressive: bid for buy, ask for sell
    output logic [31:0] order_size      // imbalance-scaled lot
);

    // -----------------------------------------------------------------------
    // Stage 0 — book_valid one cycle ago (for the 2-consecutive-cycle guard)
    // -----------------------------------------------------------------------
    logic prev_valid;

    // -----------------------------------------------------------------------
    // Stage 1 (combinational) — depth-weighted volumes + spread/eligibility
    // -----------------------------------------------------------------------
    logic [63:0] w_bid_c, w_ask_c;     // weighted volumes (wide to avoid overflow)
    logic        normal_book_c;
    logic [31:0] spread_c;
    logic        elig_c;               // snapshot eligible to fire (book stable & spread ok)

    always_comb begin
        // Weighted depth volumes: weight (NLEVELS - i) for level i (touch = NLEVELS)
        w_bid_c = '0;
        w_ask_c = '0;
        for (int i = 0; i < NLEVELS; i++) begin
            w_bid_c += 64'(NLEVELS - i) * 64'(bid_level_size[i*32 +: 32]);
            w_ask_c += 64'(NLEVELS - i) * 64'(ask_level_size[i*32 +: 32]);
        end

        normal_book_c = (best_ask_price > best_bid_price);
        spread_c      = normal_book_c ? (best_ask_price - best_bid_price) : 32'd0;
        elig_c        = book_valid && prev_valid && normal_book_c &&
                        (spread_c <= MAX_SPREAD_TICKS);
    end

    // -----------------------------------------------------------------------
    // Stage 1 registers — the imbalance cross-product inputs + book snapshot
    // -----------------------------------------------------------------------
    logic [63:0] w_bid_r, w_ask_r;
    logic [31:0] bid_p_r, ask_p_r;
    logic        elig_r;

    // -----------------------------------------------------------------------
    // Stage 2 (combinational) — cross-multiply conditions + lot sizing
    // -----------------------------------------------------------------------
    logic        buy_cond, sell_cond;
    logic [31:0] buy_size, sell_size;

    always_comb begin
        // Cross-multiply on the (registered) weighted volumes (no division)
        buy_cond  = (w_bid_r * 10 > BID_THRESH * w_ask_r) && (ask_p_r > '0);
        sell_cond = (w_ask_r * 10 > ASK_THRESH * w_bid_r) && (bid_p_r > '0);

        // Imbalance-scaled lot sizing on the weighted ratio, capped at MAX_LOT
        if (w_bid_r * 10 > 3 * BID_THRESH * w_ask_r)
            buy_size = 3 * BASE_LOT;
        else if (w_bid_r * 10 > 2 * BID_THRESH * w_ask_r)
            buy_size = 2 * BASE_LOT;
        else
            buy_size = BASE_LOT;
        if (buy_size > MAX_LOT) buy_size = MAX_LOT;

        if (w_ask_r * 10 > 3 * ASK_THRESH * w_bid_r)
            sell_size = 3 * BASE_LOT;
        else if (w_ask_r * 10 > 2 * ASK_THRESH * w_bid_r)
            sell_size = 2 * BASE_LOT;
        else
            sell_size = BASE_LOT;
        if (sell_size > MAX_LOT) sell_size = MAX_LOT;
    end

    logic [31:0] cooldown_cnt;          // counts down to 0; 0 = ready to fire
    wire  ready = elig_r && (cooldown_cnt == '0);

    // -----------------------------------------------------------------------
    // Sequential — stage-1 capture and stage-2 decision
    // -----------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            prev_valid     <= '0;
            w_bid_r        <= '0;
            w_ask_r        <= '0;
            bid_p_r        <= '0;
            ask_p_r        <= '0;
            elig_r         <= '0;
            decision_valid <= '0;
            action         <= '0;
            order_price    <= '0;
            order_size     <= '0;
            cooldown_cnt   <= '0;
        end else begin
            // ---- Stage 1: latch the weighted volumes + book snapshot ----
            prev_valid <= book_valid;
            w_bid_r    <= w_bid_c;
            w_ask_r    <= w_ask_c;
            bid_p_r    <= best_bid_price;
            ask_p_r    <= best_ask_price;
            elig_r     <= elig_c;

            // ---- Stage 2: fire the decision from the registered snapshot ----
            decision_valid <= '0;

            if (cooldown_cnt != '0)
                cooldown_cnt <= cooldown_cnt - 1'b1;

            if (ready) begin
                if (buy_cond) begin
                    decision_valid <= 1'b1;
                    action         <= 1'b0;          // BUY
                    order_price    <= ask_p_r;       // lift the ask (aggressive)
                    order_size     <= buy_size;
                    cooldown_cnt   <= COOLDOWN_CYCLES[31:0];
                end
                else if (sell_cond) begin
                    decision_valid <= 1'b1;
                    action         <= 1'b1;          // SELL
                    order_price    <= bid_p_r;       // hit the bid (aggressive)
                    order_size     <= sell_size;
                    cooldown_cnt   <= COOLDOWN_CYCLES[31:0];
                end
            end
        end
    end

endmodule
