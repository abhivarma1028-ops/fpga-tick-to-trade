// handshake_mcp — atomic multi-bit config word across clock domains
// ---------------------------------------------------------------------------
// Moves a WIDTH-bit value from `src_clk` to `dst_clk` WITHOUT a FIFO, for
// quasi-static configuration (thresholds, mode/strategy select, half-spread…)
// that changes rarely but must never be seen half-updated on the far side.
//
// Multi-Cycle-Path (MCP / "data with valid toggle") formulation:
//   1. Source latches the word into `data_hold` and flips a request toggle.
//   2. Only the 1-bit toggle is synchronized (safe); the WIDE `data_hold` bus is
//      NOT synchronized — it is held constant by the source until acknowledged,
//      so by the time the destination captures it the bits are all settled.
//   3. Destination edge-detects the toggle, registers `data_hold` into
//      `dst_data`, pulses `dst_valid`, and toggles an ack back to the source.
//   4. Source clears `src_busy` on the returned ack; a new word may then load.
//
// Because `data_hold -> dst_data` is guaranteed stable for the whole round trip,
// it is a false/multicycle path: constrain it with
//   set_max_delay -datapath_only <src_clk_period> on data_hold*/dst_data*
// (see handshake_mcp_ooc.xdc). This is the correct alternative to slapping a
// per-bit 2FF on a bus, which would let bits arrive on different cycles and
// momentarily present a value that never existed.
//
// (Production swap: Xilinx xpm_cdc_handshake — see README_cdc.md.)
// ---------------------------------------------------------------------------
module handshake_mcp #(
    parameter int WIDTH  = 32,
    parameter int STAGES = 2
)(
    input  logic             src_clk,
    input  logic             src_rst_n,
    input  logic             src_valid,           // load request (1 cycle)
    input  logic [WIDTH-1:0] src_data,
    output logic             src_ready,           // high when a new word can be loaded

    input  logic             dst_clk,
    input  logic             dst_rst_n,
    output logic             dst_valid,           // 1-cycle pulse when dst_data updates
    output logic [WIDTH-1:0] dst_data             // held stable between updates
);
    // ---- source: hold the payload, toggle req, wait for ack ----
    logic             req_tog;
    logic [WIDTH-1:0] data_hold;
    logic             ack_sync;                    // dst ack toggle, back in src domain

    assign src_ready = (req_tog == ack_sync);      // idle when req and ack agree

    always_ff @(posedge src_clk or negedge src_rst_n) begin
        if (!src_rst_n) begin
            req_tog <= 1'b0; data_hold <= '0;
        end else if (src_valid && src_ready) begin
            data_hold <= src_data;                 // stable until the next accepted load
            req_tog   <= ~req_tog;
        end
    end

    sync_2ff #(.STAGES(STAGES)) u_ack (
        .clk(src_clk), .rst_n(src_rst_n), .d(ack_tog), .q(ack_sync)
    );

    // ---- destination: sync req toggle, capture the held bus, ack ----
    logic req_d, req_d_q, ack_tog;
    sync_2ff #(.STAGES(STAGES)) u_req (
        .clk(dst_clk), .rst_n(dst_rst_n), .d(req_tog), .q(req_d)
    );

    always_ff @(posedge dst_clk or negedge dst_rst_n) begin
        if (!dst_rst_n) begin
            req_d_q <= 1'b0; ack_tog <= 1'b0;
            dst_valid <= 1'b0; dst_data <= '0;
        end else begin
            req_d_q   <= req_d;
            dst_valid <= (req_d ^ req_d_q);        // one pulse per accepted word
            if (req_d ^ req_d_q) begin
                dst_data <= data_hold;             // MCP capture (bus is settled)
                ack_tog  <= req_d;                 // mirror the toggle back as ack
            end
        end
    end
endmodule
