// cl_tick_to_trade.sv — AWS F1 Custom Logic top (Shell wrapper)
//
// Thin glue: maps the F1 Shell's OCL (AXI-Lite) bus onto cl_tick_to_trade_core,
// which holds the validated pipeline + CSR bridge. All bulk interfaces (DDR,
// DMA-PCIS, PCIM) are unused for Phase-3 bring-up — the host drives ITCH and
// reads decisions/latency entirely over OCL registers (see f1/register_map.md).
//
// ⚠ TEMPLATE: the exact Shell port list comes from the aws-fpga HDK
// (`$CL_DIR/../../common/shell_stable/.../cl_ports.vh` for your shell version).
// The body below is what must be wired; reconcile the port declaration and the
// tie-offs against the HDK `cl_ports.vh`/`cl_id_defines.vh` before building.
// The CORE it instantiates is fully verified in sim (sim/tb_cl_csr.py).

`ifndef CL_NAME
  `define CL_NAME cl_tick_to_trade
`endif

module `CL_NAME #(
    parameter int OCL_AW = 32
)(
    // ---- Clocks / resets from the Shell ----
    input  logic clk_main_a0,        // 250 MHz Shell clock domain (see note)
    input  logic rst_main_n,

    // ---- OCL AXI-Lite (BAR0 / register access) — subset actually used ----
    input  logic [OCL_AW-1:0] sh_ocl_awaddr,
    input  logic              sh_ocl_awvalid,
    output logic              ocl_sh_awready,
    input  logic [31:0]       sh_ocl_wdata,
    input  logic [3:0]        sh_ocl_wstrb,
    input  logic              sh_ocl_wvalid,
    output logic              ocl_sh_wready,
    output logic [1:0]        ocl_sh_bresp,
    output logic              ocl_sh_bvalid,
    input  logic              sh_ocl_bready,
    input  logic [OCL_AW-1:0] sh_ocl_araddr,
    input  logic              sh_ocl_arvalid,
    output logic              ocl_sh_arready,
    output logic [31:0]       ocl_sh_rdata,
    output logic [1:0]        ocl_sh_rresp,
    output logic              ocl_sh_rvalid,
    input  logic              sh_ocl_rready

    // ---- Unused Shell interfaces (declare + tie off per cl_ports.vh) ----
    //  DMA-PCIS / PCIM / DDR-C / interrupts / cl_sh_status* / sh_cl_flr*
    //  For bring-up: tie *_awready/*_wready low or per the null-CL example,
    //  drive cl_sh_id0/id1 from cl_id_defines.vh, and ack FLR.
);

    // 200 MHz design clock. The OOC constraints target 200 MHz; on the Shell,
    // either request a 200 MHz aux clock (clk_extra_*) or add a clock converter.
    // Shown here as a direct connection placeholder — wire to the 200 MHz domain.
    wire clk    = clk_main_a0;
    wire rst_n  = rst_main_n;

    // Sideband (kill switch / risk status) — expose via a status reg or LEDs.
    logic        halt;
    logic        risk_reject;
    logic [2:0]  risk_reason;
    assign halt = 1'b0;   // TODO: drive from an OCL control bit if desired

    cl_tick_to_trade_core #(.OCL_AW(OCL_AW)) u_core (
        .clk(clk), .rst_n(rst_n),
        .halt(halt), .risk_reject(risk_reject), .risk_reason(risk_reason),

        .ocl_awaddr (sh_ocl_awaddr),  .ocl_awvalid(sh_ocl_awvalid), .ocl_awready(ocl_sh_awready),
        .ocl_wdata  (sh_ocl_wdata),   .ocl_wstrb  (sh_ocl_wstrb),   .ocl_wvalid (sh_ocl_wvalid),
        .ocl_wready (ocl_sh_wready),  .ocl_bresp  (ocl_sh_bresp),   .ocl_bvalid (ocl_sh_bvalid),
        .ocl_bready (sh_ocl_bready),  .ocl_araddr (sh_ocl_araddr),  .ocl_arvalid(sh_ocl_arvalid),
        .ocl_arready(ocl_sh_arready), .ocl_rdata  (ocl_sh_rdata),   .ocl_rresp  (ocl_sh_rresp),
        .ocl_rvalid (ocl_sh_rvalid),  .ocl_rready (sh_ocl_rready)
    );

    // TODO(HDK): tie off the unused DMA-PCIS/PCIM/DDR/INT interfaces and drive
    // cl_sh_id0/id1, cl_sh_status*, FLR handshake exactly as the null-CL example
    // (cl_hello_world / cl_dram_dma) does for your shell version.

endmodule
