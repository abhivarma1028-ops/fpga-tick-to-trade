// pulse_sync — single-cycle strobe across clock domains (toggle handshake)
// ---------------------------------------------------------------------------
// A one-cycle pulse in `src_clk` becomes exactly one one-cycle pulse in
// `dst_clk`. You CANNOT just 2FF a pulse: if the source is faster than the
// destination the pulse can be narrower than a destination clock period and be
// missed entirely; if slower, one pulse can be sampled on two edges and
// double-count. The fix is to encode the event as a LEVEL TOGGLE (which is safe
// to synchronize) and edge-detect it on the far side.
//
// Closed-loop (ack) variant: the destination-side toggle is synchronized back
// into the source domain, giving a `src_busy` flag. While busy the source
// suppresses new toggles, so no event is ever lost even under bursty input —
// throughput is bounded by the round-trip latency (a few cycles each way).
// Drive one strobe, wait for !src_busy before the next.
//
//   src:  src_pulse & !src_busy  ->  flip src_tog
//   dst:  dst_pulse = edge(sync(src_tog))
//   ack:  src_busy  = src_tog ^ sync_back(dst-side toggle)
//
// For GO strobes, latency-counter clears, and other single-cycle events crossing
// domains. (Production swap: Xilinx xpm_cdc_pulse — see README_cdc.md.)
// ---------------------------------------------------------------------------
module pulse_sync #(
    parameter int STAGES = 2         // synchronizer depth each direction
)(
    input  logic src_clk,
    input  logic src_rst_n,
    input  logic src_pulse,          // one-cycle request in src domain
    output logic src_busy,           // high while a pulse is in flight; hold off new ones

    input  logic dst_clk,
    input  logic dst_rst_n,
    output logic dst_pulse           // one-cycle strobe in dst domain
);
    // ---- source: toggle a level on each accepted pulse ----
    logic src_tog;
    logic ack_sync;                  // dst-side toggle brought back to src domain
    logic tog_d, tog_d_q, dst_tog;   // dst-domain toggle chain (declare before use)

    assign src_busy = src_tog ^ ack_sync;   // differ => request still round-tripping

    always_ff @(posedge src_clk or negedge src_rst_n) begin
        if (!src_rst_n)      src_tog <= 1'b0;
        else if (src_pulse && !src_busy) src_tog <= ~src_tog;
    end

    // ---- source <- dst : synchronize the destination toggle back for the ack ----
    sync_2ff #(.STAGES(STAGES)) u_ack (
        .clk(src_clk), .rst_n(src_rst_n), .d(dst_tog), .q(ack_sync)
    );

    // ---- dst: synchronize the source toggle, edge-detect into a pulse ----
    sync_2ff #(.STAGES(STAGES)) u_fwd (
        .clk(dst_clk), .rst_n(dst_rst_n), .d(src_tog), .q(tog_d)
    );

    always_ff @(posedge dst_clk or negedge dst_rst_n) begin
        if (!dst_rst_n) begin
            tog_d_q <= 1'b0;
            dst_tog <= 1'b0;
        end else begin
            tog_d_q <= tog_d;
            dst_tog <= tog_d;        // the level fed back to the source as the ack
        end
    end

    assign dst_pulse = tog_d ^ tog_d_q;   // one dst-cycle pulse per source toggle
endmodule
