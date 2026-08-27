// Strategy Selector — runtime-selectable trading strategy for the tick-to-trade
// pipeline. Both directional strategies run in PARALLEL off the same book state;
// `strat_sel` chooses whose decision reaches the output. Adding a new algo later
// = instantiate it here and extend the mux (this is the project's "algo slot").
//
//   strat_sel = 0 : depth-weighted imbalance taker   (strategy_imbalance)
//   strat_sel = 1 : order-flow-imbalance taker (OFI)  (strategy_ofi)
//
// Both share the directional decision interface {valid, action, price, size} and
// the same 2-cycle book->decision latency, so the mux is a clean combinational
// select on the registered strategy outputs (no extra latency). The market maker
// produces two-sided QUOTES (a different interface) and lives in market_maker_top.
module strategy_select #(
    parameter int NLEVELS   = 4,
    // OFI params
    parameter int OFI_WINDOW    = 8,
    parameter int OFI_THRESHOLD = 1500,
    parameter int OFI_BASE_LOT  = 100,
    parameter int OFI_MAX_LOT   = 300
)(
    input  logic        clk,
    input  logic        rst_n,

    input  logic [1:0]  strat_sel,          // which strategy drives the output

    // Book state (from order_book_m2)
    input  logic        book_valid,
    input  logic [31:0] best_bid_price,
    input  logic [31:0] best_ask_price,
    input  logic [31:0] best_bid_size,      // level-0 sizes (OFI uses these)
    input  logic [31:0] best_ask_size,
    input  logic [NLEVELS*32-1:0] bid_level_size,   // depth (imbalance uses these)
    input  logic [NLEVELS*32-1:0] ask_level_size,

    // Selected decision out — one-cycle pulse
    output logic        decision_valid,
    output logic        action,
    output logic [31:0] order_price,
    output logic [31:0] order_size
);
    // ---- strategy 0: depth-weighted imbalance ----
    logic        imb_valid, imb_action;
    logic [31:0] imb_price, imb_size;
    strategy_imbalance #(.NLEVELS(NLEVELS)) u_imbalance (
        .clk(clk), .rst_n(rst_n),
        .book_valid(book_valid),
        .best_bid_price(best_bid_price), .best_ask_price(best_ask_price),
        .bid_level_size(bid_level_size), .ask_level_size(ask_level_size),
        .decision_valid(imb_valid), .action(imb_action),
        .order_price(imb_price), .order_size(imb_size)
    );

    // ---- strategy 1: order-flow imbalance (OFI) ----
    logic        ofi_valid, ofi_action;
    logic [31:0] ofi_price, ofi_size;
    strategy_ofi #(
        .WINDOW(OFI_WINDOW), .THRESHOLD(OFI_THRESHOLD),
        .BASE_LOT(OFI_BASE_LOT), .MAX_LOT(OFI_MAX_LOT)
    ) u_ofi (
        .clk(clk), .rst_n(rst_n),
        .book_valid(book_valid),
        .best_bid_price(best_bid_price), .best_ask_price(best_ask_price),
        .bid_size(best_bid_size), .ask_size(best_ask_size),
        .decision_valid(ofi_valid), .action(ofi_action),
        .order_price(ofi_price), .order_size(ofi_size)
    );

    // ---- mux the selected strategy's decision ----
    always_comb begin
        unique case (strat_sel)
            2'd1: begin
                decision_valid = ofi_valid;  action = ofi_action;
                order_price    = ofi_price;  order_size = ofi_size;
            end
            default: begin                    // 0 (and reserved) -> imbalance
                decision_valid = imb_valid;  action = imb_action;
                order_price    = imb_price;  order_size = imb_size;
            end
        endcase
    end

endmodule
