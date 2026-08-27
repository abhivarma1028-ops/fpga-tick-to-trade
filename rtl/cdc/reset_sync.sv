// reset_sync — asynchronous assert, synchronous release reset synchronizer
// ---------------------------------------------------------------------------
// The primitive that was missing from this library. Every other block takes an
// active-low async reset straight onto its flops' CLR pins, which is only half
// correct: asserting a reset asynchronously is fine and desirable (it works even
// with no clock running), but RELEASING it asynchronously is not.
//
// If arst_n de-asserts close to a clock edge, some flops in the domain sample
// the release and some do not. The design then leaves reset in a state that was
// never designed for -- a FIFO whose pointers disagree, a state machine one
// cycle ahead of its datapath. It is intermittent, temperature-dependent, and
// very hard to debug, which is exactly why the tool flags it: without one of
// these, Vivado reports every reset endpoint as
//   [CDC-7] Asynchronous reset unknown CDC circuitry
// (4375 of them in the cdc_brain_top block design, plus 441 CDC-1).
//
// How it works: the shift register is cleared asynchronously by arst_n, so the
// output asserts the instant the reset arrives, with no clock required. On
// release, 1s shift in one clock at a time, so rst_n rises synchronously to
// `clk` and every flop in the domain sees the same release edge. The first
// stage may go metastable if arst_n releases near an edge; the remaining stages
// give it time to settle, exactly as in sync_2ff.
//
// Instantiate ONE PER CLOCK DOMAIN, each fed from the same board/shell reset,
// and use its output as that domain's reset. Do not share one across domains --
// that would reintroduce the problem it exists to solve.
//
// STAGES defaults to 3 rather than 2: reset release fans out to every flop in
// the domain, so it is worth an extra stage of settling margin. (Production
// swap: Xilinx xpm_cdc_async_rst -- see README_cdc.md.)
// ---------------------------------------------------------------------------

module reset_sync #(
    parameter int STAGES = 3        // release-synchronizer depth (>= 2)
)(
    input  logic clk,               // destination clock
    input  logic arst_n,            // asynchronous reset in  (active low)
    output logic rst_n              // domain reset out: async assert, sync release
);

    // ASYNC_REG keeps the chain intact and placed together: never merged,
    // retimed or spread across the die.
    (* ASYNC_REG = "TRUE" *) logic [STAGES-1:0] sync;

    always_ff @(posedge clk or negedge arst_n) begin
        if (!arst_n) sync <= '0;                        // async assert
        else         sync <= {sync[STAGES-2:0], 1'b1};  // sync release
    end

    assign rst_n = sync[STAGES-1];

endmodule
