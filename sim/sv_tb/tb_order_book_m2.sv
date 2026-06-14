// =============================================================================
// tb_order_book_m2.sv  --  native SystemVerilog testbench for QuestaSim GUI
// Drives book messages and shows top-of-book + multi-level + RESCAN.
// Run:  cd sim/sv_tb && vsim -do book.do
// =============================================================================
`timescale 1ns/1ps

module tb_order_book_m2;

    localparam int NLEVELS = 4;
    localparam int RESCAN  = 260;   // cycles to let the RESCAN FSM settle

    logic clk = 0, rst_n;
    always #2.5 clk = ~clk;

    logic        msg_valid, msg_ready;
    logic [7:0]  msg_type;
    logic [63:0] order_ref, new_order_ref;
    logic        side;
    logic [31:0] shares, price;
    logic [31:0] best_bid_price, best_bid_size, best_ask_price, best_ask_size;
    logic        book_valid;
    logic [NLEVELS*32-1:0] bid_level_price, bid_level_size, ask_level_price, ask_level_size;

    localparam logic [7:0] ADD=8'h41, CANCEL=8'h58, DELETE=8'h44, EXEC=8'h45;
    localparam BID=1'b0, ASK=1'b1;

    order_book_m2 #(.NLEVELS(NLEVELS)) dut (
        .clk(clk), .rst_n(rst_n),
        .msg_valid(msg_valid), .msg_ready(msg_ready), .msg_type(msg_type),
        .order_ref(order_ref), .new_order_ref(new_order_ref),
        .side(side), .shares(shares), .price(price),
        .best_bid_price(best_bid_price), .best_bid_size(best_bid_size),
        .best_ask_price(best_ask_price), .best_ask_size(best_ask_size),
        .book_valid(book_valid),
        .bid_level_price(bid_level_price), .bid_level_size(bid_level_size),
        .ask_level_price(ask_level_price), .ask_level_size(ask_level_size)
    );

    task automatic drive(input logic [7:0] t, input logic [63:0] r, input logic s,
                         input logic [31:0] sh, input logic [31:0] p);
        msg_valid <= 1'b1; msg_type <= t; order_ref <= r; new_order_ref <= 0;
        side <= s; shares <= sh; price <= p;
        @(posedge clk);
        msg_valid <= 1'b0;
        @(posedge clk);
    endtask

    task automatic wait_rescan(); repeat (RESCAN) @(posedge clk); endtask

    task automatic show(input string tag);
        $display(" %-22s bid=%0d x%0d  ask=%0d x%0d  valid=%0b",
                 tag, best_bid_price, best_bid_size,
                 best_ask_price, best_ask_size, book_valid);
    endtask

    initial begin
        msg_valid=0; msg_type=0; order_ref=0; new_order_ref=0; side=0; shares=0; price=0;
        rst_n=0; repeat (6) @(posedge clk); rst_n=1; repeat (2) @(posedge clk);

        $display("=====================================================");
        $display(" order_book_m2 : multi-level book + RESCAN");
        $display("=====================================================");

        drive(ADD, 1, BID, 50, 1000_0000);  show("add bid  1000 x50");
        drive(ADD, 2, ASK, 20, 1100_0000);  show("add ask  1100 x20");
        drive(ADD, 3, BID, 30,  900_0000);  show("add bid   900 x30 (L1)");
        drive(ADD, 4, BID, 40, 1050_0000);  show("add bid  1050 x40 (new best)");

        drive(DELETE, 4, BID, 0, 0);         // delete the best bid -> RESCAN
        wait_rescan();                       show("delete best bid -> rescan");

        repeat (8) @(posedge clk);
        $display("=====================================================");
        $finish;
    end

    initial begin #50000; $display("FATAL timeout"); $finish; end
endmodule
