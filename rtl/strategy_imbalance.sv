// Book-Imbalance Strategy — Milestone 2 (depth-weighted)
// Signal: buy when weighted bid volume >> weighted ask volume, sell when the
// reverse. "Weighted" sums the top NLEVELS of book depth, weighting the touch
// (level 0) most heavily (weight N) down to the deepest tracked level (weight 1).
//
// Avoids division (expensive in RTL) via cross-multiply on the weighted volumes:
//   buy  signal: BID_THRESH * w_ask < w_bid * 10
//   sell signal: ASK_THRESH * w_bid < w_ask * 10
// All fixed-point integer arithmetic; no floats anywhere.
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
    // Registered decision + cooldown counter
    // -----------------------------------------------------------------------
    logic        prev_valid;
    logic [31:0] cooldown_cnt;   // counts down to 0; 0 = ready to fire

    // -----------------------------------------------------------------------
    // Depth-weighted volumes + guards / conditions (combinational)
    // -----------------------------------------------------------------------
    logic [63:0] w_bid, w_ask;   // weighted volumes (wide to avoid overflow)
    logic        normal_book;
    logic [31:0] spread;
    logic        spread_ok;
    logic        buy_cond;
    logic        sell_cond;
    logic        ready;
    logic [31:0] buy_size;
    logic [31:0] sell_size;

    always_comb begin
        // Weighted depth volumes: weight (NLEVELS - i) for level i (touch = NLEVELS)
        w_bid = '0;
        w_ask = '0;
        for (int i = 0; i < NLEVELS; i++) begin
            w_bid += 64'(NLEVELS - i) * 64'(bid_level_size[i*32 +: 32]);
            w_ask += 64'(NLEVELS - i) * 64'(ask_level_size[i*32 +: 32]);
        end

        normal_book = (best_ask_price > best_bid_price);
        spread      = normal_book ? (best_ask_price - best_bid_price) : 32'd0;
        spread_ok   = normal_book && (spread <= MAX_SPREAD_TICKS);

        // Cross-multiply on weighted volumes (no division)
        buy_cond    = (w_bid * 10 > BID_THRESH * w_ask) && (best_ask_price > '0);
        sell_cond   = (w_ask * 10 > ASK_THRESH * w_bid) && (best_bid_price > '0);

        ready       = book_valid && prev_valid && spread_ok && (cooldown_cnt == '0);

        // Imbalance-scaled lot sizing on the weighted ratio, capped at MAX_LOT
        if (w_bid * 10 > 3 * BID_THRESH * w_ask)
            buy_size = 3 * BASE_LOT;
        else if (w_bid * 10 > 2 * BID_THRESH * w_ask)
            buy_size = 2 * BASE_LOT;
        else
            buy_size = BASE_LOT;
        if (buy_size > MAX_LOT) buy_size = MAX_LOT;

        if (w_ask * 10 > 3 * ASK_THRESH * w_bid)
            sell_size = 3 * BASE_LOT;
        else if (w_ask * 10 > 2 * ASK_THRESH * w_bid)
            sell_size = 2 * BASE_LOT;
        else
            sell_size = BASE_LOT;
        if (sell_size > MAX_LOT) sell_size = MAX_LOT;
    end

    // -----------------------------------------------------------------------
    // Sequential decision
    // -----------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            decision_valid <= '0;
            action         <= '0;
            order_price    <= '0;
            order_size     <= '0;
            prev_valid     <= '0;
            cooldown_cnt   <= '0;
        end else begin
            decision_valid <= '0;
            prev_valid     <= book_valid;

            if (cooldown_cnt != '0)
                cooldown_cnt <= cooldown_cnt - 1'b1;

            if (ready) begin
                if (buy_cond) begin
                    decision_valid <= 1'b1;
                    action         <= 1'b0;             // BUY
                    order_price    <= best_ask_price;   // lift the ask (aggressive)
                    order_size     <= buy_size;
                    cooldown_cnt   <= COOLDOWN_CYCLES[31:0];
                end
                else if (sell_cond) begin
                    decision_valid <= 1'b1;
                    action         <= 1'b1;             // SELL
                    order_price    <= best_bid_price;   // hit the bid (aggressive)
                    order_size     <= sell_size;
                    cooldown_cnt   <= COOLDOWN_CYCLES[31:0];
                end
            end
        end
    end

endmodule
