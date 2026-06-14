// Multi-Level Order Book — Milestone 2
// Drop-in superset of order_book_top.sv:
//
//   1. MULTI-LEVEL: maintains the top NLEVELS price levels per side
//      (price + aggregated size), sorted best-first. Level 0 is the classic
//      top-of-book and still drives best_bid_*/best_ask_*.
//
//   2. ADD path stays O(1): a new order is inserted incrementally into the
//      level cache (side_insert), so the ~195 ns tick-to-trade latency is
//      unchanged. The full order table (entries[]) remains the source of truth.
//
//   3. RESCAN FSM: after any Cancel/Delete/Execute/Exec-with-Price/Replace, all
//      ORDER_DEPTH entries are scanned in one pass and the level cache is
//      rebuilt from scratch (correct best + depth + size aggregation).
//
// Invariant that keeps the incremental ADD correct: every order NOT in the
// level cache is strictly worse than the worst cached level, so a new ADD
// either lands in the top-N (insert/aggregate) or is worse than all of them
// (dropped) — the cache always reflects the true top-N between rescans.
//
// Known M2 limitations (fix in M3):
//   - Messages arriving during RESCAN are dropped (no backpressure yet).
//   - RESCAN runs on any C/D/E/U regardless of level; RESCAN takes
//     ORDER_DEPTH+1 cycles.

module order_book_m2 #(
    parameter ORDER_DEPTH = 256,
    parameter NLEVELS     = 4,                  // depth tracked per side
    parameter ADDR_W      = $clog2(ORDER_DEPTH)
)(
    input  logic        clk,
    input  logic        rst_n,

    // Message input (from itch_parser) — valid/ready handshake.
    // msg_ready is low during RESCAN so the parser holds the next message
    // instead of it being dropped.
    input  logic        msg_valid,
    output logic        msg_ready,
    input  logic [7:0]  msg_type,
    input  logic [63:0] order_ref,
    input  logic [63:0] new_order_ref,  // Replace(U): ref/index of the replacement order
    input  logic        side,
    input  logic [31:0] shares,
    input  logic [31:0] price,

    // Top-of-book outputs (== level 0)
    output logic [31:0] best_bid_price,
    output logic [31:0] best_bid_size,
    output logic [31:0] best_ask_price,
    output logic [31:0] best_ask_size,
    output logic        book_valid,

    // Full depth: flattened {level NLEVELS-1 .. level 0}, 32 bits each, best-first
    output logic [NLEVELS*32-1:0] bid_level_price,
    output logic [NLEVELS*32-1:0] bid_level_size,
    output logic [NLEVELS*32-1:0] ask_level_price,
    output logic [NLEVELS*32-1:0] ask_level_size
);

    localparam int LVL_CW = $clog2(NLEVELS+1);  // width to count 0..NLEVELS

    // -----------------------------------------------------------------------
    // Order storage — direct-mapped, full depth (source of truth)
    // -----------------------------------------------------------------------
    typedef struct packed {
        logic [63:0] order_ref;
        logic        side;       // 0 = bid, 1 = ask
        logic [31:0] price;
        logic [31:0] shares;
    } order_entry_t;

    order_entry_t           entries [0:ORDER_DEPTH-1];
    logic [ORDER_DEPTH-1:0] entry_valid;

    logic [ADDR_W-1:0] msg_idx;
    logic [ADDR_W-1:0] new_idx;
    assign msg_idx = order_ref[ADDR_W-1:0];
    assign new_idx = new_order_ref[ADDR_W-1:0];

    // -----------------------------------------------------------------------
    // Per-side level cache
    // -----------------------------------------------------------------------
    typedef struct packed {
        logic [NLEVELS-1:0][31:0] price;
        logic [NLEVELS-1:0][31:0] size;
        logic [LVL_CW-1:0]        cnt;   // number of filled levels (0..NLEVELS)
    } book_side_t;

    book_side_t bid_lv, ask_lv;

    // Insert (p,s) into one side's sorted top-N cache (aggregating on equal
    // price). is_bid selects descending (bids) vs ascending (asks) order.
    function automatic book_side_t side_insert(
        input book_side_t       cur,
        input logic [31:0]      p,
        input logic [31:0]      s,
        input logic             is_bid
    );
        book_side_t  r;
        logic        matched;
        logic        found_ins;
        int unsigned ins;
        r       = cur;
        matched = 1'b0;

        // 1) Aggregate if this price is already a level
        for (int unsigned i = 0; i < NLEVELS; i++) begin
            if ((i < cur.cnt) && !matched && (cur.price[i] == p)) begin
                r.size[i] = cur.size[i] + s;
                matched   = 1'b1;
            end
        end

        if (!matched) begin
            // 2) Find sorted insertion index (best-first)
            found_ins = 1'b0;
            ins       = 32'(cur.cnt);            // default: append at end
            for (int unsigned i = 0; i < NLEVELS; i++) begin
                if (!found_ins && (i < cur.cnt) &&
                    (is_bid ? (p > cur.price[i]) : (p < cur.price[i]))) begin
                    ins       = i;
                    found_ins = 1'b1;
                end
            end
            // 3) Insert if it lands within the tracked depth
            if (ins < NLEVELS) begin
                for (int unsigned i = NLEVELS-1; i > 0; i--) begin
                    if (i > ins) begin
                        r.price[i] = r.price[i-1];
                        r.size[i]  = r.size[i-1];
                    end
                end
                r.price[ins] = p;
                r.size[ins]  = s;
                r.cnt = (cur.cnt < NLEVELS[LVL_CW-1:0]) ? (cur.cnt + 1'b1) : cur.cnt;
            end
            // else: worse than all levels and cache full → dropped
        end
        return r;
    endfunction

    // -----------------------------------------------------------------------
    // Outputs derived from the level cache
    // -----------------------------------------------------------------------
    assign best_bid_price = (bid_lv.cnt != '0) ? bid_lv.price[0] : 32'h0;
    assign best_bid_size  = (bid_lv.cnt != '0) ? bid_lv.size[0]  : 32'h0;
    assign best_ask_price = (ask_lv.cnt != '0) ? ask_lv.price[0] : 32'hFFFF_FFFF;
    assign best_ask_size  = (ask_lv.cnt != '0) ? ask_lv.size[0]  : 32'h0;
    assign book_valid     = (bid_lv.cnt != '0) && (ask_lv.cnt != '0);

    genvar gi;
    generate
        for (gi = 0; gi < NLEVELS; gi++) begin : g_levels
            assign bid_level_price[gi*32 +: 32] =
                (gi < bid_lv.cnt) ? bid_lv.price[gi] : 32'h0;
            assign bid_level_size [gi*32 +: 32] =
                (gi < bid_lv.cnt) ? bid_lv.size[gi]  : 32'h0;
            assign ask_level_price[gi*32 +: 32] =
                (gi < ask_lv.cnt) ? ask_lv.price[gi] : 32'hFFFF_FFFF;
            assign ask_level_size [gi*32 +: 32] =
                (gi < ask_lv.cnt) ? ask_lv.size[gi]  : 32'h0;
        end
    endgenerate

    // -----------------------------------------------------------------------
    // FSM
    // -----------------------------------------------------------------------
    typedef enum logic { IDLE = 1'b0, RESCAN = 1'b1 } state_t;
    state_t state;

    // Ready to accept a message only when idle (RESCAN holds off the parser).
    assign msg_ready = (state == IDLE);

    // -----------------------------------------------------------------------
    // RESCAN-skip optimization: a Cancel/Delete/Execute on an order priced
    // BEYOND the tracked depth (not in the displayed top-N) cannot change any
    // shown level, so the level cache needs no rebuild. Only the full-depth
    // entries[] is updated. (Replace always rebuilds — it can promote a level.)
    // "Displayed" = the affected order's price is at/inside the worst tracked
    // level on its side, OR that side's cache isn't full (nothing was dropped).
    // -----------------------------------------------------------------------
    logic [LVL_CW-1:0] bid_widx, ask_widx;
    logic [31:0]       worst_bid_p, worst_ask_p;
    logic [31:0]       aff_price;
    logic              aff_side;
    logic              aff_displayed;

    always_comb begin
        bid_widx    = (bid_lv.cnt != '0) ? (bid_lv.cnt - 1'b1) : '0;
        ask_widx    = (ask_lv.cnt != '0) ? (ask_lv.cnt - 1'b1) : '0;
        worst_bid_p = bid_lv.price[bid_widx];
        worst_ask_p = ask_lv.price[ask_widx];

        aff_price = entries[msg_idx].price;   // pre-modification price of target
        aff_side  = entries[msg_idx].side;

        if (aff_side)  // ask: displayed if cache not full, or price <= worst shown ask
            aff_displayed = (ask_lv.cnt != NLEVELS[LVL_CW-1:0]) || (aff_price <= worst_ask_p);
        else           // bid: displayed if cache not full, or price >= worst shown bid
            aff_displayed = (bid_lv.cnt != NLEVELS[LVL_CW-1:0]) || (aff_price >= worst_bid_p);
    end

    logic [ADDR_W:0] scan_idx;            // reaches ORDER_DEPTH (commit sentinel)
    book_side_t      scan_bid_lv, scan_ask_lv;

    // Empty level cache constant
    function automatic book_side_t empty_side();
        book_side_t e;
        e = '0;          // all prices/sizes 0, cnt 0
        return e;
    endfunction

    // -----------------------------------------------------------------------
    // Main always_ff
    // -----------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bid_lv      <= '0;
            ask_lv      <= '0;
            state       <= IDLE;
            scan_idx    <= '0;
            scan_bid_lv <= '0;
            scan_ask_lv <= '0;
            entry_valid <= '0;
        end else begin
            case (state)

                // =============================================================
                // IDLE — process one incoming message per cycle
                // =============================================================
                IDLE: begin
                    if (msg_valid) begin
                        case (msg_type)

                            // ----- Add Order (A) / Add+MPID (F): O(1) -----
                            8'h41, 8'h46: begin
                                entry_valid[msg_idx]       <= 1'b1;
                                entries[msg_idx].order_ref <= order_ref;
                                entries[msg_idx].side      <= side;
                                entries[msg_idx].price     <= price;
                                entries[msg_idx].shares    <= shares;

                                if (!side)
                                    bid_lv <= side_insert(bid_lv, price, shares, 1'b1);
                                else
                                    ask_lv <= side_insert(ask_lv, price, shares, 1'b0);
                            end

                            // ----- Cancel (X) -----
                            8'h58: begin
                                if (entry_valid[msg_idx] &&
                                    entries[msg_idx].order_ref == order_ref) begin
                                    if (entries[msg_idx].shares <= shares)
                                        entry_valid[msg_idx] <= 1'b0;
                                    else
                                        entries[msg_idx].shares <=
                                            entries[msg_idx].shares - shares;

                                    // Skip RESCAN if the order isn't a displayed level
                                    if (aff_displayed) begin
                                        state       <= RESCAN;
                                        scan_idx    <= '0;
                                        scan_bid_lv <= empty_side();
                                        scan_ask_lv <= empty_side();
                                    end
                                end
                            end

                            // ----- Delete (D) -----
                            8'h44: begin
                                if (entry_valid[msg_idx] &&
                                    entries[msg_idx].order_ref == order_ref) begin
                                    entry_valid[msg_idx] <= 1'b0;

                                    if (aff_displayed) begin
                                        state       <= RESCAN;
                                        scan_idx    <= '0;
                                        scan_bid_lv <= empty_side();
                                        scan_ask_lv <= empty_side();
                                    end
                                end
                            end

                            // ----- Execute (E) / Execute-with-Price (C) -----
                            8'h45, 8'h43: begin
                                if (entry_valid[msg_idx] &&
                                    entries[msg_idx].order_ref == order_ref) begin
                                    if (entries[msg_idx].shares <= shares)
                                        entry_valid[msg_idx] <= 1'b0;
                                    else
                                        entries[msg_idx].shares <=
                                            entries[msg_idx].shares - shares;

                                    if (aff_displayed) begin
                                        state       <= RESCAN;
                                        scan_idx    <= '0;
                                        scan_bid_lv <= empty_side();
                                        scan_ask_lv <= empty_side();
                                    end
                                end
                            end

                            // ----- Replace (U) -----
                            8'h55: begin
                                if (entry_valid[msg_idx] &&
                                    entries[msg_idx].order_ref == order_ref) begin
                                    entry_valid[msg_idx] <= 1'b0;

                                    entry_valid[new_idx]       <= 1'b1;
                                    entries[new_idx].order_ref <= new_order_ref;
                                    entries[new_idx].side      <= entries[msg_idx].side;
                                    entries[new_idx].price     <= price;
                                    entries[new_idx].shares    <= shares;

                                    state       <= RESCAN;
                                    scan_idx    <= '0;
                                    scan_bid_lv <= empty_side();
                                    scan_ask_lv <= empty_side();
                                end
                            end

                            default: ;
                        endcase
                    end
                end

                // =============================================================
                // RESCAN — one entry per cycle; rebuild the level cache.
                // Cycles 0..ORDER_DEPTH-1 insert each valid entry; the extra
                // cycle at scan_idx==ORDER_DEPTH commits (NBA of the final
                // entry has settled).
                // =============================================================
                RESCAN: begin
                    if (scan_idx < ORDER_DEPTH[ADDR_W:0]) begin
                        if (entry_valid[scan_idx[ADDR_W-1:0]]) begin
                            if (!entries[scan_idx[ADDR_W-1:0]].side)
                                scan_bid_lv <= side_insert(scan_bid_lv,
                                    entries[scan_idx[ADDR_W-1:0]].price,
                                    entries[scan_idx[ADDR_W-1:0]].shares, 1'b1);
                            else
                                scan_ask_lv <= side_insert(scan_ask_lv,
                                    entries[scan_idx[ADDR_W-1:0]].price,
                                    entries[scan_idx[ADDR_W-1:0]].shares, 1'b0);
                        end
                        scan_idx <= scan_idx + 1'b1;
                    end else begin
                        bid_lv <= scan_bid_lv;
                        ask_lv <= scan_ask_lv;
                        state  <= IDLE;
                    end
                end

            endcase
        end
    end

endmodule
