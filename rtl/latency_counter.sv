// Hardware Latency Counter — tick-to-trade measurement
// Stamps the free-running clock cycle at:
//   t0: first byte of an ITCH message received (msg_start pulse)
//   t1: decision_valid pulse from strategy
// Delta = t1 - t0 = pipeline latency in clock cycles.
// Accumulates a 64-bucket histogram (bucket = delta cycles, capped at 63).
//
// AXI-Lite register map (32-bit registers):
//   0x000 – 0x0FC  : hist[0] – hist[63]   (read-only, 4 bytes each)
//   0x100          : last_latency_cycles   (read-only)
//   0x104          : clear                 (write 1 to reset histogram + last)

module latency_counter #(
    parameter HIST_BUCKETS = 64,
    parameter CNT_W        = 32,
    parameter AXIL_ADDR_W  = 9    // covers 0x000–0x1FF
)(
    input  logic clk,
    input  logic rst_n,

    // Pipeline pulses
    input  logic msg_start,       // one-cycle: first byte of new message
    input  logic decision_valid,  // one-cycle: strategy fired

    // AXI-Lite slave (read histogram from host / PS)
    // -- Write address channel
    input  logic [AXIL_ADDR_W-1:0] s_axil_awaddr,
    input  logic                   s_axil_awvalid,
    output logic                   s_axil_awready,
    // -- Write data channel
    // Only bit 0 of wdata is used (clear strobe to 0x104); wstrb is ignored
    // since the single writable register is a 1-bit command, not byte-addressable.
    // verilator lint_off UNUSEDSIGNAL
    input  logic [31:0]            s_axil_wdata,
    input  logic [3:0]             s_axil_wstrb,
    // verilator lint_on UNUSEDSIGNAL
    input  logic                   s_axil_wvalid,
    output logic                   s_axil_wready,
    // -- Write response channel
    output logic [1:0]             s_axil_bresp,
    output logic                   s_axil_bvalid,
    input  logic                   s_axil_bready,
    // -- Read address channel
    input  logic [AXIL_ADDR_W-1:0] s_axil_araddr,
    input  logic                   s_axil_arvalid,
    output logic                   s_axil_arready,
    // -- Read data channel
    output logic [31:0]            s_axil_rdata,
    output logic [1:0]             s_axil_rresp,
    output logic                   s_axil_rvalid,
    input  logic                   s_axil_rready
);

    // -----------------------------------------------------------------------
    // Free-running counter
    // -----------------------------------------------------------------------
    logic [CNT_W-1:0] free_cnt;
    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) free_cnt <= '0;
        else        free_cnt <= free_cnt + 1'b1;

    // -----------------------------------------------------------------------
    // Capture t0 on msg_start
    // -----------------------------------------------------------------------
    logic [CNT_W-1:0] t0;
    logic             measuring;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            t0        <= '0;
            measuring <= 1'b0;
        end else begin
            if (msg_start) begin
                t0        <= free_cnt;
                measuring <= 1'b1;
            end
            if (decision_valid)
                measuring <= 1'b0;
        end
    end

    // -----------------------------------------------------------------------
    // Compute delta and bin into histogram
    // -----------------------------------------------------------------------
    localparam BUCKET_W = $clog2(HIST_BUCKETS);

    logic [CNT_W-1:0]  delta;
    logic [BUCKET_W-1:0] bucket;

    assign delta  = free_cnt - t0;
    assign bucket = (delta >= HIST_BUCKETS) ? BUCKET_W'(HIST_BUCKETS-1)
                                            : delta[BUCKET_W-1:0];

    logic [CNT_W-1:0] hist [0:HIST_BUCKETS-1];
    logic [CNT_W-1:0] last_latency_cycles;
    integer idx;

    // clear strobe from AXI-Lite write to 0x104
    logic do_clear;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            last_latency_cycles <= '0;
            for (idx = 0; idx < HIST_BUCKETS; idx++) hist[idx] <= '0;
        end else if (do_clear) begin
            last_latency_cycles <= '0;
            for (idx = 0; idx < HIST_BUCKETS; idx++) hist[idx] <= '0;
        end else if (decision_valid && measuring) begin
            last_latency_cycles <= delta;
            hist[bucket]        <= hist[bucket] + 1'b1;
        end
    end

    // -----------------------------------------------------------------------
    // AXI-Lite slave — single-cycle read latency, one outstanding txn
    // -----------------------------------------------------------------------

    // ── Write path ──────────────────────────────────────────────────────────
    // Accept address and data together (both channels must be valid).
    // Only register 0x104 is writable; all others return OKAY but are ignored.

    logic aw_active;   // address has been accepted, waiting for wdata
    logic [AXIL_ADDR_W-1:0] aw_addr_lat;

    assign s_axil_awready = !aw_active && !s_axil_bvalid;
    assign s_axil_wready  = aw_active  && !s_axil_bvalid;
    assign s_axil_bresp   = 2'b00; // OKAY

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            aw_active    <= 1'b0;
            aw_addr_lat  <= '0;
            s_axil_bvalid <= 1'b0;
            do_clear     <= 1'b0;
        end else begin
            do_clear <= 1'b0; // default

            // Latch write address
            if (s_axil_awvalid && s_axil_awready) begin
                aw_active   <= 1'b1;
                aw_addr_lat <= s_axil_awaddr;
            end

            // Accept write data
            if (s_axil_wvalid && s_axil_wready) begin
                aw_active    <= 1'b0;
                s_axil_bvalid <= 1'b1;
                // 0x104 = clear register
                if (aw_addr_lat == AXIL_ADDR_W'('h104) && s_axil_wdata[0])
                    do_clear <= 1'b1;
            end

            // Clear bvalid once master accepts response
            if (s_axil_bvalid && s_axil_bready)
                s_axil_bvalid <= 1'b0;
        end
    end

    // ── Read path ────────────────────────────────────────────────────────────
    // One-cycle pipeline: accept araddr → present rdata the next cycle.

    logic                   ar_active;
    logic [AXIL_ADDR_W-1:0] ar_addr_lat;

    assign s_axil_arready = !ar_active && !s_axil_rvalid;
    assign s_axil_rresp   = 2'b00; // OKAY

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ar_active    <= 1'b0;
            ar_addr_lat  <= '0;
            s_axil_rvalid <= 1'b0;
            s_axil_rdata  <= '0;
        end else begin
            // Latch read address
            if (s_axil_arvalid && s_axil_arready) begin
                ar_active   <= 1'b1;
                ar_addr_lat <= s_axil_araddr;
            end

            // Present data one cycle after address accepted
            if (ar_active && !s_axil_rvalid) begin
                ar_active    <= 1'b0;
                s_axil_rvalid <= 1'b1;

                // Address decode
                if (ar_addr_lat[AXIL_ADDR_W-1:8] == '0) begin
                    // 0x000–0x0FC  →  hist[addr[7:2]]
                    s_axil_rdata <= hist[ar_addr_lat[7:2]];
                end else if (ar_addr_lat == AXIL_ADDR_W'('h100)) begin
                    s_axil_rdata <= last_latency_cycles;
                end else begin
                    s_axil_rdata <= 32'hDEAD_BEEF; // undefined register
                end
            end

            // Clear rvalid once master accepts data
            if (s_axil_rvalid && s_axil_rready)
                s_axil_rvalid <= 1'b0;
        end
    end

endmodule
