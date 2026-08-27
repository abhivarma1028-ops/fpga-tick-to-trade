// sync_2ff — single-bit multi-flop clock-domain-crossing synchronizer
// ---------------------------------------------------------------------------
// The workhorse CDC primitive: bring ONE asynchronous bit into `clk` through a
// chain of STAGES flip-flops. The first flop may go metastable when the input
// changes near the sampling edge; the remaining flop(s) give it time to resolve,
// so the MTBF grows exponentially with STAGES. Two stages is the standard; use
// three for very high-frequency / high-reliability domains.
//
// USE ONLY FOR SINGLE-BIT signals that are level-stable long enough to be
// sampled (quasi-static controls, or one bit whose exact capture cycle doesn't
// matter — e.g. `halt`). For a MULTI-BIT bus you must NOT instantiate one of
// these per bit: the bits would resolve on different cycles and the destination
// could momentarily see a value that never existed. Cross buses with a
// Gray-coded async_fifo (data) or handshake_mcp (config) instead.
//
// The ASYNC_REG attribute tells synthesis/place-and-route these are
// synchronizer flops: keep them, don't optimize them away, and place them close
// together to maximise the metastability settling window. (Production swap:
// Xilinx xpm_cdc_single — see README_cdc.md.)
// ---------------------------------------------------------------------------
module sync_2ff #(
    parameter int STAGES   = 2,      // synchronizer depth (>= 2)
    parameter bit INIT_VAL = 1'b0    // reset / power-up value of the chain
)(
    input  logic clk,               // destination clock
    input  logic rst_n,             // destination-domain async reset (active low)
    input  logic d,                 // asynchronous bit (source domain)
    output logic q                  // synchronized bit (in `clk` domain)
);
    (* ASYNC_REG = "TRUE" *) logic [STAGES-1:0] sync;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            sync <= {STAGES{INIT_VAL}};
        else
            sync <= {sync[STAGES-2:0], d};   // shift toward the MSB
    end

    assign q = sync[STAGES-1];
endmodule
