// =============================================================================
// tb_strategy_imbalance.sv  --  native SystemVerilog testbench for QuestaSim GUI
// Drives book states (level-0 + depth) and shows the BUY/SELL decision + lot.
// Run:  cd sim/sv_tb && vsim -do strategy.do
// =============================================================================
`timescale 1ns/1ps

module tb_strategy_imbalance;

    localparam int NLEVELS = 4;

    logic clk = 0, rst_n;
    always #2.5 clk = ~clk;

    logic        book_valid;
    logic [31:0] best_bid_price, best_ask_price;
    logic [NLEVELS*32-1:0] bid_level_size, ask_level_size;
    logic        decision_valid, action;
    logic [31:0] order_price, order_size;

    strategy_imbalance #(.NLEVELS(NLEVELS)) dut (
        .clk(clk), .rst_n(rst_n),
        .book_valid(book_valid),
        .best_bid_price(best_bid_price), .best_ask_price(best_ask_price),
        .bid_level_size(bid_level_size), .ask_level_size(ask_level_size),
        .decision_valid(decision_valid), .action(action),
        .order_price(order_price), .order_size(order_size)
    );

    // pack up to NLEVELS sizes (level 0 first) into the flattened bus
    function automatic logic [NLEVELS*32-1:0] pack(input int unsigned s0, s1, s2, s3);
        pack = '0;
        pack[0*32 +: 32] = s0; pack[1*32 +: 32] = s1;
        pack[2*32 +: 32] = s2; pack[3*32 +: 32] = s3;
    endfunction

    task automatic set_book(input int unsigned bp, input int unsigned ap,
                            input int unsigned b0,b1,b2,b3, input int unsigned a0,a1,a2,a3);
        book_valid     <= 1'b1;
        best_bid_price <= bp; best_ask_price <= ap;
        bid_level_size <= pack(b0,b1,b2,b3);
        ask_level_size <= pack(a0,a1,a2,a3);
    endtask

    task automatic await(input string tag);
        for (int c=0;c<20;c++) begin
            @(posedge clk);
            if (decision_valid) begin
                $display(" %-26s -> %s  price=%0d  size=%0d",
                         tag, action ? "SELL" : "BUY", order_price, order_size);
                return;
            end
        end
        $display(" %-26s -> (no decision)", tag);
    endtask

    initial begin
        book_valid=0; best_bid_price=0; best_ask_price=0; bid_level_size=0; ask_level_size=0;
        rst_n=0; repeat (6) @(posedge clk); rst_n=1; repeat (2) @(posedge clk);

        $display("=====================================================");
        $display(" strategy_imbalance : depth-weighted signal + lots");
        $display("=====================================================");

        // touch-only 2:1 bid imbalance -> BUY, base lot
        set_book(1_499_900, 1_500_100, 200,0,0,0, 100,0,0,0); await("touch 2:1 bid");
        rst_n=0; repeat(3) @(posedge clk); rst_n=1; @(posedge clk);

        // strong imbalance -> larger lot (capped)
        set_book(1_499_900, 1_500_100, 1000,0,0,0, 100,0,0,0); await("strong bid (cap)");
        rst_n=0; repeat(3) @(posedge clk); rst_n=1; @(posedge clk);

        // balanced touch but bid supported in depth -> BUY
        set_book(1_499_900, 1_500_100, 100,100,100,100, 100,0,0,0); await("depth-supported bid");
        rst_n=0; repeat(3) @(posedge clk); rst_n=1; @(posedge clk);

        // ask-heavy -> SELL
        set_book(1_499_900, 1_500_100, 100,0,0,0, 300,0,0,0); await("ask 3:1");

        repeat (8) @(posedge clk);
        $display("=====================================================");
        $finish;
    end

    initial begin #20000; $display("FATAL timeout"); $finish; end
endmodule
