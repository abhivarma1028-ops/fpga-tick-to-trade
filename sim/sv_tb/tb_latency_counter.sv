// =============================================================================
// tb_latency_counter.sv  --  native SystemVerilog testbench for QuestaSim GUI
// Pulses msg_start..decision_valid, then reads the histogram over AXI-Lite.
// Run:  cd sim/sv_tb && vsim -do latency.do
// =============================================================================
`timescale 1ns/1ps

module tb_latency_counter;

    logic clk = 0, rst_n;
    always #2.5 clk = ~clk;

    logic        msg_start, decision_valid;
    logic [8:0]  s_axil_awaddr;  logic s_axil_awvalid, s_axil_awready;
    logic [31:0] s_axil_wdata;   logic [3:0] s_axil_wstrb;
    logic        s_axil_wvalid,  s_axil_wready;
    logic [1:0]  s_axil_bresp;   logic s_axil_bvalid, s_axil_bready;
    logic [8:0]  s_axil_araddr;  logic s_axil_arvalid, s_axil_arready;
    logic [31:0] s_axil_rdata;   logic [1:0] s_axil_rresp;
    logic        s_axil_rvalid,  s_axil_rready;

    latency_counter dut (
        .clk(clk), .rst_n(rst_n),
        .msg_start(msg_start), .decision_valid(decision_valid),
        .s_axil_awaddr(s_axil_awaddr), .s_axil_awvalid(s_axil_awvalid), .s_axil_awready(s_axil_awready),
        .s_axil_wdata(s_axil_wdata),   .s_axil_wstrb(s_axil_wstrb),
        .s_axil_wvalid(s_axil_wvalid), .s_axil_wready(s_axil_wready),
        .s_axil_bresp(s_axil_bresp),   .s_axil_bvalid(s_axil_bvalid), .s_axil_bready(s_axil_bready),
        .s_axil_araddr(s_axil_araddr), .s_axil_arvalid(s_axil_arvalid), .s_axil_arready(s_axil_arready),
        .s_axil_rdata(s_axil_rdata),   .s_axil_rresp(s_axil_rresp),
        .s_axil_rvalid(s_axil_rvalid), .s_axil_rready(s_axil_rready)
    );

    // one measurement: msg_start, wait gap cycles, decision_valid
    task automatic measure(input int gap);
        msg_start <= 1'b1; @(posedge clk); msg_start <= 1'b0;
        repeat (gap-1) @(posedge clk);
        decision_valid <= 1'b1; @(posedge clk); decision_valid <= 1'b0;
        repeat (2) @(posedge clk);
    endtask

    task automatic axil_read(input logic [8:0] a, output logic [31:0] d);
        s_axil_araddr <= a; s_axil_arvalid <= 1'b1; s_axil_rready <= 1'b1;
        @(posedge clk);
        while (s_axil_arready !== 1'b1) @(posedge clk);
        s_axil_arvalid <= 1'b0;
        while (s_axil_rvalid !== 1'b1) @(posedge clk);
        d = s_axil_rdata; @(posedge clk); s_axil_rready <= 1'b0;
    endtask

    logic [31:0] rd;
    initial begin
        msg_start=0; decision_valid=0;
        s_axil_awaddr=0; s_axil_awvalid=0; s_axil_wdata=0; s_axil_wstrb=0;
        s_axil_wvalid=0; s_axil_bready=0; s_axil_araddr=0; s_axil_arvalid=0; s_axil_rready=0;
        rst_n=0; repeat (6) @(posedge clk); rst_n=1; repeat (2) @(posedge clk);

        $display("=====================================================");
        $display(" latency_counter : measure + AXI-Lite histogram read");
        $display("=====================================================");

        measure(10);  // 10-cycle latency
        measure(10);  // again -> bucket 10 count = 2
        measure(15);

        axil_read(9'h100, rd); $display(" last latency  (0x100) = %0d cycles", rd);
        axil_read(9'd40,  rd); $display(" hist[bucket 10] (0x28) = %0d", rd);  // 10*4 = 0x28
        axil_read(9'd60,  rd); $display(" hist[bucket 15] (0x3C) = %0d", rd);  // 15*4 = 0x3C

        repeat (6) @(posedge clk);
        $display("=====================================================");
        $finish;
    end

    initial begin #20000; $display("FATAL timeout"); $finish; end
endmodule
