// Tick-to-Trade Top — wires parser → book → strategy → decision output
// AXI-Stream slave in (ITCH bytes), AXI-Stream master out (decision packets)
// AXI-Lite slave out — latency histogram (see latency_counter.sv for reg map)

module tick_to_trade_top (
    input  logic        clk,
    input  logic        rst_n,

    // ITCH byte stream in (from DMA)
    input  logic        s_axis_tvalid,
    output logic        s_axis_tready,
    input  logic [7:0]  s_axis_tdata,
    input  logic        s_axis_tlast,

    // Decision stream out (to DMA → PS → IBKR bridge)
    output logic        m_axis_tvalid,
    // verilator lint_off UNUSEDSIGNAL
    input  logic        m_axis_tready, // no back-pressure: one beat per decision
    // verilator lint_on UNUSEDSIGNAL
    output logic [71:0] m_axis_tdata,  // {action[7:0], price[31:0], size[31:0]}
    output logic        m_axis_tlast,

    // AXI-Lite slave — latency counter histogram readout
    input  logic [8:0]  s_axil_awaddr,
    input  logic        s_axil_awvalid,
    output logic        s_axil_awready,
    input  logic [31:0] s_axil_wdata,
    input  logic [3:0]  s_axil_wstrb,
    input  logic        s_axil_wvalid,
    output logic        s_axil_wready,
    output logic [1:0]  s_axil_bresp,
    output logic        s_axil_bvalid,
    input  logic        s_axil_bready,
    input  logic [8:0]  s_axil_araddr,
    input  logic        s_axil_arvalid,
    output logic        s_axil_arready,
    output logic [31:0] s_axil_rdata,
    output logic [1:0]  s_axil_rresp,
    output logic        s_axil_rvalid,
    input  logic        s_axil_rready
);

    // -----------------------------------------------------------------------
    // Parser
    // -----------------------------------------------------------------------
    logic        parser_valid;
    logic        book_ready;       // book→parser backpressure handshake
    logic [7:0]  msg_type;
    // verilator lint_off UNUSEDSIGNAL
    logic [47:0] timestamp;   // parsed but not consumed by strategy (M1/M2 scope)
    // verilator lint_on UNUSEDSIGNAL
    logic [63:0] order_ref;
    logic [63:0] new_order_ref;
    logic        side;
    logic [31:0] shares, price;

    itch_parser u_parser (
        .clk            (clk),
        .rst_n          (rst_n),
        .s_axis_tvalid  (s_axis_tvalid),
        .s_axis_tready  (s_axis_tready),
        .s_axis_tdata   (s_axis_tdata),
        .s_axis_tlast   (s_axis_tlast),
        .m_valid        (parser_valid),
        .m_ready        (book_ready),
        .msg_type       (msg_type),
        .timestamp      (timestamp),
        .order_ref      (order_ref),
        .new_order_ref  (new_order_ref),
        .side           (side),
        .shares         (shares),
        .price          (price),
        /* verilator lint_off PINCONNECTEMPTY */
        .msg_unsupported()  // unsupported-message flag not used downstream
        /* verilator lint_on PINCONNECTEMPTY */
    );

    // -----------------------------------------------------------------------
    // Order book (M2: multi-level + RESCAN FSM)
    // -----------------------------------------------------------------------
    localparam int NLEVELS = 4;

    logic [31:0] bbid_p, bask_p;
    logic        book_valid;
    // verilator lint_off UNUSEDSIGNAL
    logic [31:0] bbid_s, bask_s;                             // best sizes (== level 0) unused
    logic [NLEVELS*32-1:0] bid_level_price, ask_level_price; // level prices unused by strategy
    // verilator lint_on UNUSEDSIGNAL
    logic [NLEVELS*32-1:0] bid_level_size,  ask_level_size;

    order_book_m2 #(.NLEVELS(NLEVELS)) u_book (
        .clk             (clk),
        .rst_n           (rst_n),
        .msg_valid       (parser_valid),
        .msg_ready       (book_ready),
        .msg_type        (msg_type),
        .order_ref       (order_ref),
        .new_order_ref   (new_order_ref),
        .side            (side),
        .shares          (shares),
        .price           (price),
        .best_bid_price  (bbid_p),
        .best_bid_size   (bbid_s),
        .best_ask_price  (bask_p),
        .best_ask_size   (bask_s),
        .book_valid      (book_valid),
        .bid_level_price (bid_level_price),
        .bid_level_size  (bid_level_size),
        .ask_level_price (ask_level_price),
        .ask_level_size  (ask_level_size)
    );

    // -----------------------------------------------------------------------
    // Strategy (depth-weighted over NLEVELS)
    // -----------------------------------------------------------------------
    logic        dec_valid;
    logic        action;
    logic [31:0] order_price, order_size;

    strategy_imbalance #(.NLEVELS(NLEVELS)) u_strategy (
        .clk            (clk),
        .rst_n          (rst_n),
        .book_valid     (book_valid),
        .best_bid_price (bbid_p),
        .best_ask_price (bask_p),
        .bid_level_size (bid_level_size),
        .ask_level_size (ask_level_size),
        .decision_valid (dec_valid),
        .action         (action),
        .order_price    (order_price),
        .order_size     (order_size)
    );

    // -----------------------------------------------------------------------
    // Latency counter
    // -----------------------------------------------------------------------
    // msg_start: first byte of each new message
    logic msg_start;
    logic [5:0] byte_cnt_mon;
    wire  rx = s_axis_tvalid & s_axis_tready;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) byte_cnt_mon <= '0;
        else if (rx) begin
            if (s_axis_tlast) byte_cnt_mon <= '0;
            else              byte_cnt_mon <= byte_cnt_mon + 1'b1;
        end
    end
    assign msg_start = rx & (byte_cnt_mon == '0);

    latency_counter u_lat (
        .clk              (clk),
        .rst_n            (rst_n),
        .msg_start        (msg_start),
        .decision_valid   (dec_valid),
        .s_axil_awaddr    (s_axil_awaddr),
        .s_axil_awvalid   (s_axil_awvalid),
        .s_axil_awready   (s_axil_awready),
        .s_axil_wdata     (s_axil_wdata),
        .s_axil_wstrb     (s_axil_wstrb),
        .s_axil_wvalid    (s_axil_wvalid),
        .s_axil_wready    (s_axil_wready),
        .s_axil_bresp     (s_axil_bresp),
        .s_axil_bvalid    (s_axil_bvalid),
        .s_axil_bready    (s_axil_bready),
        .s_axil_araddr    (s_axil_araddr),
        .s_axil_arvalid   (s_axil_arvalid),
        .s_axil_arready   (s_axil_arready),
        .s_axil_rdata     (s_axil_rdata),
        .s_axil_rresp     (s_axil_rresp),
        .s_axil_rvalid    (s_axil_rvalid),
        .s_axil_rready    (s_axil_rready)
    );

    // -----------------------------------------------------------------------
    // Decision output — pack into AXI-Stream beat
    // -----------------------------------------------------------------------
    assign m_axis_tvalid = dec_valid;
    assign m_axis_tdata  = {{7{1'b0}}, action, order_price, order_size};
    assign m_axis_tlast  = dec_valid; // one beat per decision

endmodule
