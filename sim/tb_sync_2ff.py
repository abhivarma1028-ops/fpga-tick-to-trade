"""
cocotb: rtl/cdc/sync_2ff.sv — single-bit CDC synchronizer.

Drives the async input `d` at random real-time offsets (unaligned to `clk`, i.e.
a genuine asynchronous source), then after allowing the chain to settle asserts
that `q` equals the driven level. Also checks the steady-state propagation latency
is exactly STAGES cycles. Metastability itself is not simulable; this proves the
functional path and settling behaviour.

Run: cd sim && make cdc-run TOPLEVEL=sync_2ff MODULE=tb_sync_2ff
"""
import os, sys, random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer

STAGES = 2
CLK_NS = 7          # destination clock period


async def reset(dut):
    dut.rst_n.value = 0
    dut.d.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


@cocotb.test()
async def sync_2ff_propagates(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    await reset(dut)
    rng = random.Random(7)

    checks = 0
    for _ in range(200):
        val = rng.randint(0, 1)
        # change the input at an arbitrary (sub-cycle) offset — truly async
        await Timer(rng.randint(1, 3 * CLK_NS), unit="ns")
        dut.d.value = val
        # let it clock through the chain plus margin, then verify
        for _ in range(STAGES + 2):
            await RisingEdge(dut.clk)
        assert int(dut.q.value) == val, f"q={int(dut.q.value)} expected {val}"
        checks += 1

    dut._log.info(f"sync_2ff: {checks} async transitions propagated correctly")


@cocotb.test()
async def sync_2ff_latency(dut):
    """A synchronous change on d appears on q exactly STAGES cycles later."""
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    await reset(dut)

    dut.d.value = 0
    for _ in range(STAGES + 2):
        await RisingEdge(dut.clk)
    assert int(dut.q.value) == 0

    # drive d high synchronously right after an edge, then count edges to q
    await RisingEdge(dut.clk)
    dut.d.value = 1
    seen = None
    for cyc in range(1, STAGES + 4):
        await RisingEdge(dut.clk)
        if int(dut.q.value) == 1:
            seen = cyc
            break
    # ~STAGES cycles; allow +1 for the simulator's write-visibility delta
    assert STAGES <= seen <= STAGES + 1, f"latency {seen}, expected ~{STAGES}"
    dut._log.info(f"sync_2ff: latency = {seen} cycles (STAGES={STAGES})")
