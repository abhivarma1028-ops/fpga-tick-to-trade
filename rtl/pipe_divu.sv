// Pipelined unsigned integer divider — W stages, one restoring-division step per
// stage, throughput 1/cycle, fixed latency = W cycles. Each stage is a single
// W-bit compare-subtract (short carry chain) so it closes timing at 200 MHz,
// unlike a single-cycle divide (which is ~W carry chains deep).
//
//   quo = num / den   (truncating, matches Python // for non-negative operands)
//
// A `payload` travels alongside the operands (same latency) so caller-side data
// (e.g. inventory) lines up with the quotient. `in_valid` pipelines to out_valid.
module pipe_divu #(
    parameter int W  = 64,     // operand / quotient width
    parameter int PW = 32      // payload width carried alongside
)(
    input  logic          clk,
    input  logic          rst_n,
    input  logic          in_valid,
    input  logic [W-1:0]  num,
    input  logic [W-1:0]  den,
    input  logic [PW-1:0] payload,
    output logic          out_valid,
    output logic [W-1:0]  quo,
    output logic [PW-1:0] payload_o
);
    // per-stage pipeline registers (index 0 = inputs, 1..W = after each step)
    logic [W-1:0]  rem   [0:W];   // running remainder
    logic [W-1:0]  quot  [0:W];   // quotient being built
    logic [W-1:0]  nsh   [0:W];   // remaining numerator (MSB-first)
    logic [W-1:0]  dreg  [0:W];   // divisor carried down
    logic          vld   [0:W];
    logic [PW-1:0] pl    [0:W];

    // stage 0 — combinational load of the inputs
    always_comb begin
        rem[0]  = '0;
        quot[0] = '0;
        nsh[0]  = num;
        dreg[0] = den;
        vld[0]  = in_valid;
        pl[0]   = payload;
    end

    genvar i;
    generate
        for (i = 0; i < W; i++) begin : gstage
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    rem[i+1] <= '0; quot[i+1] <= '0; nsh[i+1] <= '0;
                    dreg[i+1] <= '0; vld[i+1] <= 1'b0; pl[i+1] <= '0;
                end else begin
                    logic [W-1:0] r;
                    r = (rem[i] << 1) | {{(W-1){1'b0}}, nsh[i][W-1]};  // bring down next numerator bit
                    if (r >= dreg[i] && dreg[i] != 0) begin
                        rem[i+1]  <= r - dreg[i];
                        quot[i+1] <= (quot[i] << 1) | {{(W-1){1'b0}}, 1'b1};
                    end else begin
                        rem[i+1]  <= r;
                        quot[i+1] <= (quot[i] << 1);
                    end
                    nsh[i+1]  <= nsh[i] << 1;
                    dreg[i+1] <= dreg[i];
                    vld[i+1]  <= vld[i];
                    pl[i+1]   <= pl[i];
                end
            end
        end
    endgenerate

    assign out_valid = vld[W];
    assign quo       = quot[W];
    assign payload_o = pl[W];
endmodule
