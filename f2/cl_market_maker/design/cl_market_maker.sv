// ============================================================================
// cl_market_maker — F2 Custom Logic top for the hardware MARKET-MAKER accelerator
//
// Mirrors cl_tick_to_trade.sv (same AWS F2 Shell boilerplate: PCIM/PCIS/SDA/DDR/
// HBM/JTAG tie-offs) but hangs market_maker_top off the OCL AXI-Lite control port
// instead of the tick-to-trade core.
//
// OCL AXI-Lite register map (market_maker_top):
//   0x000-0x1FF : latency_counter histogram + last_latency@0x100 + clear@0x104
//   0x200 W best_bid_price   0x214 W GO strobe    0x220 R status{quote_valid}
//   0x204 W best_ask_price   0x224 R bid_price    0x228 R bid_qty
//   0x208 W bid_size         0x22C R ask_price    0x230 R ask_qty
//   0x20C W ask_size
//   0x210 W inventory(signed)
//
// market_maker_top is timing-closed (WNS +1.004 ns @200 MHz, xcu200 OOC) and
// cocotb-verified (40/40 CSR checks). This wrapper makes it AFI-buildable via the
// AWS HDK exactly like cl_tick_to_trade.
// ============================================================================

module cl_market_maker
    #(
      parameter EN_DDR = 0,
      parameter EN_HBM = 0
    )
    (
      `include "cl_ports.vh"
    );

`include "cl_id_defines.vh"
`include "cl_market_maker_defines.vh"

//=============================================================================
// GLOBALS
//=============================================================================
  always_comb begin
     cl_sh_flr_done    = 'b1;
     cl_sh_status0     = 'b0;
     cl_sh_status1     = 'b0;
     cl_sh_status2     = 'b0;
     cl_sh_id0         = `CL_SH_ID0;
     cl_sh_id1         = `CL_SH_ID1;
     cl_sh_status_vled = 'b0;
     cl_sh_dma_wr_full = 'b0;
     cl_sh_dma_rd_full = 'b0;
  end

//=============================================================================
// PCIM (tie-off)
//=============================================================================
  always_comb begin
    cl_sh_pcim_awaddr='b0; cl_sh_pcim_awsize='b0; cl_sh_pcim_awburst='b0; cl_sh_pcim_awvalid='b0;
    cl_sh_pcim_wdata='b0;  cl_sh_pcim_wstrb='b0;  cl_sh_pcim_wlast='b0;   cl_sh_pcim_wvalid='b0;
    cl_sh_pcim_araddr='b0; cl_sh_pcim_arsize='b0; cl_sh_pcim_arburst='b0; cl_sh_pcim_arvalid='b0;
  end
  always_comb begin
    cl_sh_pcim_awid='b0; cl_sh_pcim_awlen='b0; cl_sh_pcim_awcache='b0; cl_sh_pcim_awlock='b0;
    cl_sh_pcim_awprot='b0; cl_sh_pcim_awqos='b0; cl_sh_pcim_awuser='b0;
    cl_sh_pcim_wid='b0; cl_sh_pcim_wuser='b0;
    cl_sh_pcim_arid='b0; cl_sh_pcim_arlen='b0; cl_sh_pcim_arcache='b0; cl_sh_pcim_arlock='b0;
    cl_sh_pcim_arprot='b0; cl_sh_pcim_arqos='b0; cl_sh_pcim_aruser='b0;
    cl_sh_pcim_rready='b0;
  end

//=============================================================================
// PCIS (tie-off)
//=============================================================================
  always_comb begin
    cl_sh_dma_pcis_bresp='b0; cl_sh_dma_pcis_rresp='b0; cl_sh_dma_pcis_rvalid='b0;
  end
  always_comb begin
    cl_sh_dma_pcis_awready='b0; cl_sh_dma_pcis_wready='b0;
    cl_sh_dma_pcis_bid='b0; cl_sh_dma_pcis_bvalid='b0;
    cl_sh_dma_pcis_arready='b0;
    cl_sh_dma_pcis_rid='b0; cl_sh_dma_pcis_rdata='b0; cl_sh_dma_pcis_rlast='b0; cl_sh_dma_pcis_ruser='b0;
  end

//=============================================================================
// OCL  -> market_maker_top   (Shell ocl_cl_* / cl_ocl_*  ->  core s_*)
//=============================================================================
  market_maker_top #(
    .HALF_SPREAD  (200),
    .SKEW         (2),
    .QUOTE_SIZE   (100),
    .MAX_POSITION (1000),
    .AW           (12)
  ) u_mm_top (
    .clk        (clk_main_a0),
    .rst_n      (rst_main_n),
    // OCL is 32-bit addressed; the top decodes the low 12 bits (0x000-0xFFF)
    .s_awaddr   (ocl_cl_awaddr[11:0]),
    .s_awvalid  (ocl_cl_awvalid),
    .s_awready  (cl_ocl_awready),
    .s_wdata    (ocl_cl_wdata),
    .s_wstrb    (ocl_cl_wstrb),
    .s_wvalid   (ocl_cl_wvalid),
    .s_wready   (cl_ocl_wready),
    .s_bresp    (cl_ocl_bresp),
    .s_bvalid   (cl_ocl_bvalid),
    .s_bready   (ocl_cl_bready),
    .s_araddr   (ocl_cl_araddr[11:0]),
    .s_arvalid  (ocl_cl_arvalid),
    .s_arready  (cl_ocl_arready),
    .s_rdata    (cl_ocl_rdata),
    .s_rresp    (cl_ocl_rresp),
    .s_rvalid   (cl_ocl_rvalid),
    .s_rready   (ocl_cl_rready)
  );

//=============================================================================
// SDA (tie-off)
//=============================================================================
  always_comb begin
    cl_sda_bresp='b0; cl_sda_rresp='b0; cl_sda_rvalid='b0;
  end
  always_comb begin
    cl_sda_awready='b0; cl_sda_wready='b0; cl_sda_bvalid='b0; cl_sda_arready='b0; cl_sda_rdata='b0;
  end

//=============================================================================
// SH_DDR
//=============================================================================
   sh_ddr #(.DDR_PRESENT (EN_DDR)) SH_DDR (
      .clk(clk_main_a0), .rst_n(), .stat_clk(clk_main_a0), .stat_rst_n(),
      .CLK_DIMM_DP(CLK_DIMM_DP), .CLK_DIMM_DN(CLK_DIMM_DN),
      .M_ACT_N(M_ACT_N), .M_MA(M_MA), .M_BA(M_BA), .M_BG(M_BG), .M_CKE(M_CKE),
      .M_ODT(M_ODT), .M_CS_N(M_CS_N), .M_CLK_DN(M_CLK_DN), .M_CLK_DP(M_CLK_DP),
      .M_PAR(M_PAR), .M_DQ(M_DQ), .M_ECC(M_ECC), .M_DQS_DP(M_DQS_DP), .M_DQS_DN(M_DQS_DN),
      .cl_RST_DIMM_N(RST_DIMM_N),
      .cl_sh_ddr_axi_awid(), .cl_sh_ddr_axi_awaddr(), .cl_sh_ddr_axi_awlen(),
      .cl_sh_ddr_axi_awsize(), .cl_sh_ddr_axi_awvalid(), .cl_sh_ddr_axi_awburst(),
      .cl_sh_ddr_axi_awuser(), .cl_sh_ddr_axi_awready(), .cl_sh_ddr_axi_wdata(),
      .cl_sh_ddr_axi_wstrb(), .cl_sh_ddr_axi_wlast(), .cl_sh_ddr_axi_wvalid(),
      .cl_sh_ddr_axi_wready(), .cl_sh_ddr_axi_bid(), .cl_sh_ddr_axi_bresp(),
      .cl_sh_ddr_axi_bvalid(), .cl_sh_ddr_axi_bready(), .cl_sh_ddr_axi_arid(),
      .cl_sh_ddr_axi_araddr(), .cl_sh_ddr_axi_arlen(), .cl_sh_ddr_axi_arsize(),
      .cl_sh_ddr_axi_arvalid(), .cl_sh_ddr_axi_arburst(), .cl_sh_ddr_axi_aruser(),
      .cl_sh_ddr_axi_arready(), .cl_sh_ddr_axi_rid(), .cl_sh_ddr_axi_rdata(),
      .cl_sh_ddr_axi_rresp(), .cl_sh_ddr_axi_rlast(), .cl_sh_ddr_axi_rvalid(),
      .cl_sh_ddr_axi_rready(), .sh_ddr_stat_bus_addr(), .sh_ddr_stat_bus_wdata(),
      .sh_ddr_stat_bus_wr(), .sh_ddr_stat_bus_rd(), .sh_ddr_stat_bus_ack(),
      .sh_ddr_stat_bus_rdata(), .ddr_sh_stat_int(), .sh_cl_ddr_is_ready()
   );
  always_comb begin
    cl_sh_ddr_stat_ack='b0; cl_sh_ddr_stat_rdata='b0; cl_sh_ddr_stat_int='b0;
  end

//=============================================================================
// INTERRUPTS / JTAG / HBM / PCIE (tie-off)
//=============================================================================
  always_comb cl_sh_apppf_irq_req = 'b0;
  always_comb tdo = 'b0;
  always_comb begin
    hbm_apb_paddr_1='b0; hbm_apb_pprot_1='b0; hbm_apb_psel_1='b0; hbm_apb_penable_1='b0;
    hbm_apb_pwrite_1='b0; hbm_apb_pwdata_1='b0; hbm_apb_pstrb_1='b0; hbm_apb_pready_1='b0;
    hbm_apb_prdata_1='b0; hbm_apb_pslverr_1='b0;
    hbm_apb_paddr_0='b0; hbm_apb_pprot_0='b0; hbm_apb_psel_0='b0; hbm_apb_penable_0='b0;
    hbm_apb_pwrite_0='b0; hbm_apb_pwdata_0='b0; hbm_apb_pstrb_0='b0; hbm_apb_pready_0='b0;
    hbm_apb_prdata_0='b0; hbm_apb_pslverr_0='b0;
  end
  always_comb begin
    PCIE_EP_TXP='b0; PCIE_EP_TXN='b0; PCIE_RP_PERSTN='b0; PCIE_RP_TXP='b0; PCIE_RP_TXN='b0;
  end

endmodule // cl_market_maker
