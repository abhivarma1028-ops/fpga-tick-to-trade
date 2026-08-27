// async_fifo — dual-clock (asynchronous) FIFO with Gray-coded pointers
// ---------------------------------------------------------------------------
// The canonical way to move a MULTI-BIT data stream between two unrelated clocks.
// After Cummings, "Simulation and Synthesis Techniques for Asynchronous FIFO
// Design" (SNUG 2002).
//
// Why Gray pointers: the write and read pointers must be compared across the
// boundary to derive full/empty. A binary pointer changes many bits at once, so
// a synchronized sample could catch it mid-transition and yield a wildly wrong
// value. A Gray code changes exactly ONE bit per increment, so a metastable
// sample resolves to either the old or the new pointer — never a bogus
// in-between — which is off by at most one entry (conservatively safe: full may
// assert one early, empty one late; the FIFO never over/under-flows).
//
// Pointers are ADDR_W+1 bits: the extra MSB distinguishes "full" (pointers equal
// except top two bits) from "empty" (pointers exactly equal).
//
// Read is FIRST-WORD FALL-THROUGH: `rd_data` continuously shows the head and is
// valid whenever `!empty`; assert `rd_en` for one `rd_clk` cycle to pop. This is
// the natural shape for an AXI-Stream wrapper (tdata=head, tvalid=!empty,
// pop on tvalid&&tready). (Production swap: Xilinx xpm_fifo_async — README_cdc.md.)
// ---------------------------------------------------------------------------
module async_fifo #(
    parameter int WIDTH    = 8,
    parameter int DEPTH    = 16,      // must be a power of two
    parameter int STAGES   = 2,       // pointer-synchronizer depth
    parameter int AF_LEVEL = DEPTH-2, // almost_full asserts at >= this occupancy
    parameter int AE_LEVEL = 1        // almost_empty asserts at <= this occupancy
)(
    // ---- write domain ----
    input  logic             wr_clk,
    input  logic             wr_rst_n,
    input  logic             wr_en,
    input  logic [WIDTH-1:0] wr_data,
    output logic             full,
    output logic             almost_full,

    // ---- read domain ----
    input  logic             rd_clk,
    input  logic             rd_rst_n,
    input  logic             rd_en,
    output logic [WIDTH-1:0] rd_data,
    output logic             empty,
    output logic             almost_empty
);
    localparam int AW = $clog2(DEPTH);

    // storage — simple dual-port; async read of the head (distributed RAM)
    logic [WIDTH-1:0] mem [0:DEPTH-1];

    // binary + gray pointers, each AW+1 bits
    logic [AW:0] wbin, wgray, rbin, rgray;

    function automatic logic [AW:0] bin2gray(input logic [AW:0] b);
        return b ^ (b >> 1);
    endfunction
    function automatic logic [AW:0] gray2bin(input logic [AW:0] g);
        logic [AW:0] b;
        for (int i = AW; i >= 0; i--) b[i] = ^(g >> i);
        return b;
    endfunction

    // cross-domain synchronized Gray pointers (per-bit 2FF is safe on Gray code)
    (* ASYNC_REG = "TRUE" *) logic [AW:0] rq [0:STAGES-1]; // read gray  -> write clk
    (* ASYNC_REG = "TRUE" *) logic [AW:0] wq [0:STAGES-1]; // write gray -> read clk

    always_ff @(posedge wr_clk or negedge wr_rst_n) begin
        if (!wr_rst_n) for (int i=0;i<STAGES;i++) rq[i] <= '0;
        else begin
            rq[0] <= rgray;
            for (int i=1;i<STAGES;i++) rq[i] <= rq[i-1];
        end
    end
    always_ff @(posedge rd_clk or negedge rd_rst_n) begin
        if (!rd_rst_n) for (int i=0;i<STAGES;i++) wq[i] <= '0;
        else begin
            wq[0] <= wgray;
            for (int i=1;i<STAGES;i++) wq[i] <= wq[i-1];
        end
    end
    wire [AW:0] rgray_s = rq[STAGES-1];   // read pointer seen in write domain
    wire [AW:0] wgray_s = wq[STAGES-1];   // write pointer seen in read domain

    // ===================== write domain =====================
    wire         do_wr    = wr_en && !full;
    wire [AW:0]  wbin_nxt = wbin + (AW+1)'(do_wr);
    wire [AW:0]  wgray_nxt= bin2gray(wbin_nxt);
    // full: next write gray == read gray with the top two bits inverted
    wire full_nxt = (wgray_nxt == {~rgray_s[AW:AW-1], rgray_s[AW-2:0]});

    // the array lives in its own reset-free block: a RAM written inside an
    // async-reset always_ff cannot infer as BRAM/LUTRAM (Synth 8-4767)
    always_ff @(posedge wr_clk) begin
        if (do_wr) mem[wbin[AW-1:0]] <= wr_data;
    end

    always_ff @(posedge wr_clk or negedge wr_rst_n) begin
        if (!wr_rst_n) begin
            wbin<='0; wgray<='0; full<=1'b0;
        end else begin
            wbin  <= wbin_nxt;
            wgray <= wgray_nxt;
            full  <= full_nxt;
        end
    end
    // occupancy in the write domain (read pointer converted back to binary)
    wire [AW:0] wr_used = wbin - gray2bin(rgray_s);
    assign almost_full = (wr_used >= (AW+1)'(AF_LEVEL));

    // ===================== read domain ======================
    wire         do_rd    = rd_en && !empty;
    wire [AW:0]  rbin_nxt = rbin + (AW+1)'(do_rd);
    wire [AW:0]  rgray_nxt= bin2gray(rbin_nxt);
    wire empty_nxt = (rgray_nxt == wgray_s);   // caught up to the write pointer

    always_ff @(posedge rd_clk or negedge rd_rst_n) begin
        if (!rd_rst_n) begin
            rbin<='0; rgray<='0; empty<=1'b1;
        end else begin
            rbin  <= rbin_nxt;
            rgray <= rgray_nxt;
            empty <= empty_nxt;
        end
    end
    assign rd_data = mem[rbin[AW-1:0]];        // first-word fall-through
    // occupancy in the read domain (write pointer converted back to binary)
    wire [AW:0] rd_used = gray2bin(wgray_s) - rbin;
    assign almost_empty = (rd_used <= (AW+1)'(AE_LEVEL));
endmodule
