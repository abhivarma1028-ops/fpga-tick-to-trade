// axis_async_fifo — AXI-Stream clock-crossing FIFO
// ---------------------------------------------------------------------------
// Thin AXI4-Stream wrapper around async_fifo. This is the drop-in component at a
// datapath clock crossing (e.g. RX-line -> Core on the ITCH byte stream, or
// Core -> Host on the decision stream) and the natural IP-Integrator boundary:
// package it with the two AXIS interfaces + two clocks and it auto-connects.
//
//   slave  side (s_axis) is clocked by s_clk  (producer domain)
//   master side (m_axis) is clocked by m_clk  (consumer domain)
//
// {tlast, tdata} ride together through one async_fifo so framing is preserved
// across the crossing. Standard ready/valid back-pressure on both ends: the
// producer stalls when the FIFO is full, the consumer sees tvalid only when data
// is present (first-word fall-through). tkeep/tuser can be widened into TDATA if
// needed. (Production swap: Xilinx "AXI4-Stream Data FIFO" IP with independent
// clocks, or "AXI4-Stream Clock Converter" — see README_cdc.md.)
// ---------------------------------------------------------------------------
module axis_async_fifo #(
    parameter int TDATA_W = 8,
    parameter int DEPTH   = 16,
    parameter int STAGES  = 2
)(
    // ---- slave / write side (producer clock) ----
    input  logic               s_clk,
    input  logic               s_rst_n,
    input  logic               s_axis_tvalid,
    output logic               s_axis_tready,
    input  logic [TDATA_W-1:0] s_axis_tdata,
    input  logic               s_axis_tlast,

    // ---- master / read side (consumer clock) ----
    input  logic               m_clk,
    input  logic               m_rst_n,
    output logic               m_axis_tvalid,
    input  logic               m_axis_tready,
    output logic [TDATA_W-1:0] m_axis_tdata,
    output logic               m_axis_tlast
);
    localparam int W = TDATA_W + 1;             // {tlast, tdata}

    logic             full, empty;
    logic [W-1:0]     wr_data, rd_data;

    assign wr_data       = {s_axis_tlast, s_axis_tdata};
    assign s_axis_tready = !full;

    assign m_axis_tvalid = !empty;
    assign {m_axis_tlast, m_axis_tdata} = rd_data;

    async_fifo #(.WIDTH(W), .DEPTH(DEPTH), .STAGES(STAGES)) u_fifo (
        .wr_clk(s_clk), .wr_rst_n(s_rst_n),
        .wr_en(s_axis_tvalid && !full), .wr_data(wr_data),
        .full(full), .almost_full(),
        .rd_clk(m_clk), .rd_rst_n(m_rst_n),
        .rd_en(m_axis_tvalid && m_axis_tready), .rd_data(rd_data),
        .empty(empty), .almost_empty()
    );
endmodule
