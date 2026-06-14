// Top-of-Book Order Book — Milestone 1
// Tracks best bid and best ask from Add/Cancel/Delete/Execute messages.
// Uses a small register-file hash (ORDER_DEPTH entries) to map order_ref →
// {price, shares, side}. Best bid/ask updated on every message.
//
// Limitation (M1): fixed-size order table; collision not handled.
// Milestone 2 replaces this with a BRAM price-indexed multi-level book.

module order_book_top #(
    parameter ORDER_DEPTH = 256,       // number of live orders tracked
    parameter ADDR_W      = $clog2(ORDER_DEPTH)
)(
    input  logic        clk,
    input  logic        rst_n,

    // Message input (from itch_parser)
    input  logic        msg_valid,
    input  logic [7:0]  msg_type,
    input  logic [63:0] order_ref,
    input  logic        side,
    input  logic [31:0] shares,
    input  logic [31:0] price,

    // Top-of-book outputs (updated one cycle after msg_valid)
    output logic [31:0] best_bid_price,
    output logic [31:0] best_bid_size,
    output logic [31:0] best_ask_price,
    output logic [31:0] best_ask_size,
    output logic        book_valid      // high once at least one bid+ask seen
);

    // -----------------------------------------------------------------------
    // Order storage — direct-mapped by order_ref[ADDR_W-1:0]
    // ('table' is a reserved SV keyword; use 'entries' instead)
    // -----------------------------------------------------------------------
    typedef struct packed {
        logic        valid;
        logic [63:0] order_ref;
        logic        side;
        logic [31:0] price;
        logic [31:0] shares;
    } order_entry_t;

    order_entry_t entries [0:ORDER_DEPTH-1];

    logic [ADDR_W-1:0] idx;
    assign idx = order_ref[ADDR_W-1:0];

    // -----------------------------------------------------------------------
    // Top-of-book registers
    // -----------------------------------------------------------------------
    logic [31:0] best_bid_p, best_bid_s;
    logic [31:0] best_ask_p, best_ask_s;
    logic        bid_seen, ask_seen;

    assign best_bid_price = best_bid_p;
    assign best_bid_size  = best_bid_s;
    assign best_ask_price = best_ask_p;
    assign best_ask_size  = best_ask_s;
    assign book_valid     = bid_seen & ask_seen;

    // -----------------------------------------------------------------------
    // Message processing
    // -----------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bid_seen   <= '0;
            ask_seen   <= '0;
            best_bid_p <= '0;
            best_bid_s <= '0;
            best_ask_p <= 32'hFFFF_FFFF;
            best_ask_s <= '0;
            for (int i = 0; i < ORDER_DEPTH; i++)
                entries[i] <= '0;
        end else if (msg_valid) begin

            case (msg_type)

                8'h41: begin // Add Order
                    entries[idx].valid     <= 1'b1;
                    entries[idx].order_ref <= order_ref;
                    entries[idx].side      <= side;
                    entries[idx].price     <= price;
                    entries[idx].shares    <= shares;

                    if (!side) begin // buy
                        bid_seen <= 1'b1;
                        if (price > best_bid_p || !bid_seen) begin
                            best_bid_p <= price;
                            best_bid_s <= shares;
                        end else if (price == best_bid_p) begin
                            best_bid_s <= best_bid_s + shares;
                        end
                    end else begin // sell
                        ask_seen <= 1'b1;
                        if (price < best_ask_p || !ask_seen) begin
                            best_ask_p <= price;
                            best_ask_s <= shares;
                        end else if (price == best_ask_p) begin
                            best_ask_s <= best_ask_s + shares;
                        end
                    end
                end

                8'h58: begin // Cancel — reduce shares
                    if (entries[idx].valid && entries[idx].order_ref == order_ref) begin
                        if (entries[idx].shares <= shares)
                            entries[idx].valid <= 1'b0;
                        else
                            entries[idx].shares <= entries[idx].shares - shares;
                        // TODO M2: re-scan for new best after cancel at best level
                    end
                end

                8'h44: begin // Delete
                    if (entries[idx].valid && entries[idx].order_ref == order_ref)
                        entries[idx].valid <= 1'b0;
                    // TODO M2: re-scan for new best after delete at best level
                end

                8'h45: begin // Execute — reduce shares
                    if (entries[idx].valid && entries[idx].order_ref == order_ref) begin
                        if (entries[idx].shares <= shares)
                            entries[idx].valid <= 1'b0;
                        else
                            entries[idx].shares <= entries[idx].shares - shares;
                        // TODO M2: re-scan for new best
                    end
                end

                default: ;
            endcase
        end
    end

endmodule
