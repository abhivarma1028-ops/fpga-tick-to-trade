"""
cocotb test for rtl/market_maker_top.sv — the MM accelerator with AXI-Lite CSR.

Drives the accelerator exactly as a host would: write the top-of-book + inventory
to control registers, strobe GO, then read the quote (bid/ask price+qty) back over
AXI-Lite and check it against sim/golden/mm_rtl.py. Also reads the latency counter.

Run:  cd sim && make sim SIM=verilator TOPLEVEL=market_maker_top MODULE=tb_market_maker_top \
        EXTRA_ARGS="--trace --trace-fst -Wno-PINMISSING -Wno-WIDTHEXPAND"
"""
import os, sys, random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

sys.path.insert(0, os.path.dirname(__file__))
from golden.mm_rtl import mm_quote

P = dict(HALF_SPREAD=200, SKEW=2, QUOTE_SIZE=100, MAX_POSITION=1000)

# register map (word addresses)
BID_PX, ASK_PX, BID_SZ, ASK_SZ, INV, GO = 0x200,0x204,0x208,0x20C,0x210,0x214
ST, R_BIDP, R_BIDQ, R_ASKP, R_ASKQ = 0x220,0x224,0x228,0x22C,0x230
LAT_LAST, LAT_CLEAR = 0x100, 0x104


async def axil_write(dut, addr, data):
    dut.s_awaddr.value = addr; dut.s_awvalid.value = 1
    dut.s_wdata.value = data & 0xFFFFFFFF; dut.s_wstrb.value = 0xF; dut.s_wvalid.value = 1
    while True:
        await RisingEdge(dut.clk)
        if dut.s_awready.value and dut.s_wready.value:
            break
    dut.s_awvalid.value = 0; dut.s_wvalid.value = 0
    dut.s_bready.value = 1
    while not dut.s_bvalid.value:
        await RisingEdge(dut.clk)
    await RisingEdge(dut.clk); dut.s_bready.value = 0


async def axil_read(dut, addr):
    dut.s_araddr.value = addr; dut.s_arvalid.value = 1; dut.s_rready.value = 1
    while not dut.s_arready.value:
        await RisingEdge(dut.clk)
    await RisingEdge(dut.clk); dut.s_arvalid.value = 0
    while not dut.s_rvalid.value:
        await RisingEdge(dut.clk)
    val = int(dut.s_rdata.value)
    await RisingEdge(dut.clk); dut.s_rready.value = 0
    return val


async def reset(dut):
    for s in ('s_awvalid','s_wvalid','s_bready','s_arvalid','s_rready'):
        getattr(dut, s).value = 0
    dut.rst_n.value = 0
    for _ in range(4): await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


@cocotb.test()
async def mm_top_csr(dut):
    cocotb.start_soon(Clock(dut.clk, 5, units="ns").start())
    await reset(dut)
    rng = random.Random(3)
    checks = mism = 0

    for _ in range(40):
        mid = rng.randint(500_000, 5_000_000); half = rng.randint(1, 4000)
        bp, ap = mid-half, mid+half
        bs, asz = rng.randint(1, 3000), rng.randint(1, 3000)
        inv = rng.randint(-1200, 1200)
        # program the book + inventory
        await axil_write(dut, BID_PX, bp); await axil_write(dut, ASK_PX, ap)
        await axil_write(dut, BID_SZ, bs); await axil_write(dut, ASK_SZ, asz)
        await axil_write(dut, INV, inv & 0xFFFFFFFF)
        await axil_write(dut, GO, 1)                    # strobe -> compute
        # wait out the pipeline (input reg + 64 div + out reg + latch)
        for _ in range(72): await RisingEdge(dut.clk)
        st = await axil_read(dut, ST)
        got = (st & 1,
               await axil_read(dut, R_BIDP), await axil_read(dut, R_BIDQ),
               await axil_read(dut, R_ASKP), await axil_read(dut, R_ASKQ))
        exp = mm_quote(1, bp, ap, bs, asz, inv, **P)   # book_valid=1 (GO)
        if got != exp:
            mism += 1
            dut._log.error(f"CSR quote mismatch exp {exp} got {got} "
                           f"(bp={bp} ap={ap} bs={bs} as={asz} inv={inv})")
        checks += 1

    lat = await axil_read(dut, LAT_LAST)
    dut._log.info(f"MM-top CSR: {checks} checks, {mism} mismatches, last_latency={lat} cycles")
    assert mism == 0, f"{mism} mismatches"
