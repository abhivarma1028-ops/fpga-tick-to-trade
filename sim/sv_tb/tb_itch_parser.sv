// =============================================================================
// tb_itch_parser.sv  --  native SystemVerilog testbench for QuestaSim GUI
// Drives a few ITCH messages and shows the decoded fields + handshake.
// Run:  cd sim/sv_tb && vsim -do parser.do
// =============================================================================
`timescale 1ns/1ps

module tb_itch_parser;

    logic clk = 0, rst_n;
    always #2.5 clk = ~clk;             // 200 MHz

    logic        s_axis_tvalid, s_axis_tready, s_axis_tlast;
    logic [7:0]  s_axis_tdata;
    logic        m_valid, m_ready;
    logic [7:0]  msg_type;
    logic [47:0] timestamp;
    logic [63:0] order_ref, new_order_ref;
    logic        side;
    logic [31:0] shares, price;
    logic        msg_unsupported;

    itch_parser dut (
        .clk(clk), .rst_n(rst_n),
        .s_axis_tvalid(s_axis_tvalid), .s_axis_tready(s_axis_tready),
        .s_axis_tdata(s_axis_tdata),   .s_axis_tlast(s_axis_tlast),
        .m_valid(m_valid), .m_ready(m_ready),
        .msg_type(msg_type), .timestamp(timestamp),
        .order_ref(order_ref), .new_order_ref(new_order_ref),
        .side(side), .shares(shares), .price(price),
        .msg_unsupported(msg_unsupported)
    );

    // ---- message builders --------------------------------------------------
    function automatic void make_add(ref byte unsigned q[$], input bit is_sell,
            input longint unsigned oref, input int unsigned shares_i, input int unsigned price_i);
        q = {};
        q.push_back(8'h41);
        q.push_back(8'h00); q.push_back(8'h01); q.push_back(8'h00); q.push_back(8'h00);
        for (int i=0;i<6;i++) q.push_back(8'h00);
        for (int i=7;i>=0;i--) q.push_back(oref[8*i +: 8]);
        q.push_back(is_sell ? 8'h53 : 8'h42);
        for (int i=3;i>=0;i--) q.push_back(shares_i[8*i +: 8]);
        for (int i=0;i<8;i++) q.push_back(8'h20);
        for (int i=3;i>=0;i--) q.push_back(price_i[8*i +: 8]);
    endfunction

    // Replace (U, 35B): orig_ref, new_ref, shares, price
    function automatic void make_replace(ref byte unsigned q[$],
            input longint unsigned oref, input longint unsigned nref,
            input int unsigned shares_i, input int unsigned price_i);
        q = {};
        q.push_back(8'h55);
        q.push_back(8'h00); q.push_back(8'h01); q.push_back(8'h00); q.push_back(8'h00);
        for (int i=0;i<6;i++) q.push_back(8'h00);
        for (int i=7;i>=0;i--) q.push_back(oref[8*i +: 8]);
        for (int i=7;i>=0;i--) q.push_back(nref[8*i +: 8]);
        for (int i=3;i>=0;i--) q.push_back(shares_i[8*i +: 8]);
        for (int i=3;i>=0;i--) q.push_back(price_i[8*i +: 8]);
    endfunction

    task automatic axis_send(input byte unsigned q[$]);
        foreach (q[i]) begin
            s_axis_tvalid <= 1'b1; s_axis_tdata <= q[i];
            s_axis_tlast  <= (i == q.size()-1);
            @(posedge clk);
            while (s_axis_tready !== 1'b1) @(posedge clk);
        end
        s_axis_tvalid <= 1'b0; s_axis_tlast <= 1'b0;
    endtask

    task automatic show(input string name);
        // wait for the decoded pulse
        for (int c=0;c<60;c++) begin
            @(posedge clk);
            if (m_valid) begin
                $display(" %-10s type=%h ref=%0d new_ref=%0d side=%0d shares=%0d price=%0d",
                         name, msg_type, order_ref, new_order_ref, side, shares, price);
                return;
            end
        end
        $display(" %-10s : NO m_valid (timeout)", name);
    endtask

    byte unsigned msg[$];
    initial begin
        s_axis_tvalid=0; s_axis_tdata=0; s_axis_tlast=0; m_ready=1;
        rst_n=0; repeat (6) @(posedge clk); rst_n=1; repeat (2) @(posedge clk);

        $display("=====================================================");
        $display(" itch_parser : byte stream -> decoded fields");
        $display("=====================================================");

        make_add(msg, 1'b0, 64'd42, 32'd500, 32'd1_234_567); axis_send(msg); show("Add(B)");
        make_add(msg, 1'b1, 64'd99, 32'd200, 32'd9_990_000); axis_send(msg); show("Add(S)");
        make_replace(msg, 64'd42, 64'd77, 32'd250, 32'd1_600_000); axis_send(msg); show("Replace");

        repeat (8) @(posedge clk);
        $display("=====================================================");
        $finish;
    end

    initial begin #20000; $display("FATAL timeout"); $finish; end
endmodule
