// Post-implementation TIMING simulation testbench for tick_to_trade_top.
// Drives the BUY scenario (ask @1_500_100 then bid @1_499_900, bid-heavy 200 vs
// 100) into the gate-level netlist with SDF back-annotated (real cell + routing
// delays), confirms the BUY decision is produced correctly at the 5 ns (200 MHz)
// clock, and reads the hardware latency-counter value over AXI-Lite.
`timescale 1ps/1ps

module tb_timesim;

  localparam realtime CLK_PS = 5000;   // 5 ns = 200 MHz

  logic clk = 0;
  logic rst_n;
  // s_axis (ITCH in)
  logic        s_axis_tvalid;
  logic        s_axis_tready;
  logic [7:0]  s_axis_tdata;
  logic        s_axis_tlast;
  // risk
  logic        halt;
  logic        risk_reject;
  logic [2:0]  risk_reason;
  // m_axis (decision out)
  logic        m_axis_tvalid;
  logic        m_axis_tready;
  logic [71:0] m_axis_tdata;
  logic        m_axis_tlast;
  // AXI-Lite
  logic [8:0]  s_axil_awaddr;  logic s_axil_awvalid; logic s_axil_awready;
  logic [31:0] s_axil_wdata;   logic [3:0] s_axil_wstrb; logic s_axil_wvalid; logic s_axil_wready;
  logic [1:0]  s_axil_bresp;   logic s_axil_bvalid; logic s_axil_bready;
  logic [8:0]  s_axil_araddr;  logic s_axil_arvalid; logic s_axil_arready;
  logic [31:0] s_axil_rdata;   logic [1:0] s_axil_rresp; logic s_axil_rvalid; logic s_axil_rready;

  always #(CLK_PS/2) clk = ~clk;

  tick_to_trade_top dut (.*);

  // ---- ITCH messages (exact bytes from synth_itch.py, body w/o length) ----
  localparam int LEN = 36;
  logic [7:0] ASK [0:LEN-1] = '{
    8'h41, 8'h00, 8'h01, 8'h00, 8'h00, 8'h1F, 8'h1A, 8'hCE, 8'hD9, 8'hF3,
    8'hE8, 8'h00, 8'h00, 8'h00, 8'h00, 8'h00, 8'h00, 8'h00, 8'h01, 8'h53,
    8'h00, 8'h00, 8'h00, 8'h64, 8'h54, 8'h45, 8'h53, 8'h54, 8'h20, 8'h20,
    8'h20, 8'h20, 8'h00, 8'h16, 8'hE3, 8'hC4 };
  logic [7:0] BID [0:LEN-1] = '{
    8'h41, 8'h00, 8'h01, 8'h00, 8'h00, 8'h1F, 8'h1A, 8'hCE, 8'hD9, 8'hF7,
    8'hD0, 8'h00, 8'h00, 8'h00, 8'h00, 8'h00, 8'h00, 8'h00, 8'h02, 8'h42,
    8'h00, 8'h00, 8'h00, 8'hC8, 8'h54, 8'h45, 8'h53, 8'h54, 8'h20, 8'h20,
    8'h20, 8'h20, 8'h00, 8'h16, 8'hE2, 8'hFC };

  int cyc;                 // free-running cycle counter
  int t_bid_start, t_dec;  // cycle stamps

  always @(posedge clk) cyc <= cyc + 1;

  // AXI-Stream master: present a byte, hold it until the cycle where tready is
  // sampled high (byte accepted), then advance. Drive a touch after the edge to
  // avoid racing the SDF delays at the clock edge.
  task automatic drive_msg(input logic [7:0] msg [0:LEN-1]);
    for (int i = 0; i < LEN; i++) begin
      s_axis_tvalid = 1'b1;
      s_axis_tdata  = msg[i];
      s_axis_tlast  = (i == LEN-1);
      do @(posedge clk); while (s_axis_tready !== 1'b1);  // wait until accepted
    end
    s_axis_tvalid = 1'b0;
    s_axis_tlast  = 1'b0;
    s_axis_tdata  = 8'h00;
  endtask

  task automatic axil_read(input logic [8:0] addr, output logic [31:0] data);
    @(posedge clk);
    s_axil_araddr  <= addr; s_axil_arvalid <= 1'b1; s_axil_rready <= 1'b1;
    @(posedge clk);
    while (!s_axil_arready) @(posedge clk);
    s_axil_arvalid <= 1'b0;
    while (!s_axil_rvalid) @(posedge clk);
    data = s_axil_rdata;
    @(posedge clk);
    s_axil_rready  <= 1'b0;
  endtask

  logic [31:0] last_latency;
  int action; longint price, size;

  initial begin
    // init
    rst_n=0; s_axis_tvalid=0; s_axis_tdata=0; s_axis_tlast=0; halt=0;
    m_axis_tready=1; cyc=0;
    s_axil_awaddr=0; s_axil_awvalid=0; s_axil_wdata=0; s_axil_wstrb=0; s_axil_wvalid=0;
    s_axil_bready=1; s_axil_araddr=0; s_axil_arvalid=0; s_axil_rready=0;
    repeat (30) @(posedge clk);   // hold past glbl GSR (~100 ns) before release
    rst_n = 1;
    repeat (12) @(posedge clk);

    $display("[%0t] driving ASK message", $time);
    drive_msg(ASK);
    repeat (8) @(posedge clk);
    t_bid_start = cyc;
    $display("[%0t] driving BID message (triggers BUY)", $time);
    drive_msg(BID);

    // wait for the decision
    fork
      begin
        wait (m_axis_tvalid === 1'b1);
        t_dec = cyc;
      end
      begin
        repeat (400) @(posedge clk);
        $display("ERROR: timeout waiting for decision"); $finish;
      end
    join_any
    disable fork;

    action = m_axis_tdata[71:64];
    price  = m_axis_tdata[63:32];
    size   = m_axis_tdata[31:0];
    $display("=================================================");
    $display(" GATE-LEVEL TIMING SIM RESULT (SDF back-annotated)");
    $display("   decision: %s  price=%0d  size=%0d",
             (action==0)?"BUY":"SELL", price, size);
    $display("   m_axis_tvalid at cycle %0d (bid started %0d) -> %0d cycles",
             t_dec, t_bid_start, t_dec - t_bid_start);

    // authoritative hardware latency from the counter (0x100)
    repeat (4) @(posedge clk);
    axil_read(9'h100, last_latency);
    $display("   latency_counter last_latency = %0d cycles = %0d ns @200MHz",
             last_latency, last_latency*5);
    $display("=================================================");

    if (action==0 && price==1500100)
      $display("PASS: correct BUY decision under real gate delays");
    else
      $display("FAIL: unexpected decision (action=%0d price=%0d)", action, price);

    repeat (10) @(posedge clk);
    $finish;
  end

  // safety net
  initial begin #5_000_000; $display("GLOBAL TIMEOUT"); $finish; end

endmodule
