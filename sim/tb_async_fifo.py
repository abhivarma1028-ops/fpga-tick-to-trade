"""
cocotb: rtl/cdc/async_fifo.sv — Gray-pointer dual-clock FIFO.

Independent write/read clocks at different rates. A producer pushes a known
increasing sequence whenever `!full` (random throttle); a consumer pops whenever
`!empty` (random throttle) and records `rd_data`. Pass condition: the popped
sequence is an in-order, loss/dup-free prefix of the pushed sequence
(golden.async_fifo.check_prefix), and all N items come out.

Drive/sample happen on the *falling* edge of each clock so they are clean of the
RTL's rising-edge pointer updates.

Run: cd sim && make cdc-run TOPLEVEL=async_fifo MODULE=tb_async_fifo
"""
import os, sys, random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, Timer

sys.path.insert(0, os.path.dirname(__file__))
from golden.async_fifo import check_prefix

N = 200            # values 0..199 fit the default 8-bit WIDTH (all unique)


async def _reset(dut):
    dut.wr_rst_n.value = 0
    dut.rd_rst_n.value = 0
    dut.wr_en.value = 0
    dut.rd_en.value = 0
    dut.wr_data.value = 0
    await Timer(60, unit="ns")
    dut.wr_rst_n.value = 1
    dut.rd_rst_n.value = 1
    await RisingEdge(dut.wr_clk)
    await RisingEdge(dut.rd_clk)


async def _producer(dut, pushed, seed):
    rng = random.Random(seed)
    i = 0
    while i < N:
        await FallingEdge(dut.wr_clk)
        if int(dut.full.value):
            assert int(dut.almost_full.value), "full asserted but almost_full low"
            dut.wr_en.value = 0
            continue
        if rng.random() < 0.75:
            dut.wr_data.value = i
            dut.wr_en.value = 1
            pushed.append(i)
            i += 1
        else:
            dut.wr_en.value = 0
    await RisingEdge(dut.wr_clk)     # let the final asserted write commit
    dut.wr_en.value = 0


async def _consumer(dut, popped, seed):
    rng = random.Random(seed)
    guard = 0
    while len(popped) < N:
        await FallingEdge(dut.rd_clk)
        guard += 1
        assert guard < 200_000, "consumer stalled — FIFO not draining"
        if int(dut.empty.value) == 0 and rng.random() < 0.5:
            popped.append(int(dut.rd_data.value))
            dut.rd_en.value = 1
        else:
            dut.rd_en.value = 0
    dut.rd_en.value = 0


@cocotb.test()
async def async_fifo_integrity(dut):
    cocotb.start_soon(Clock(dut.wr_clk, 5, unit="ns").start())   # fast writer
    cocotb.start_soon(Clock(dut.rd_clk, 11, unit="ns").start())  # slow reader
    await _reset(dut)

    pushed, popped = [], []
    prod = cocotb.start_soon(_producer(dut, pushed, seed=4))
    cons = cocotb.start_soon(_consumer(dut, popped, seed=5))
    await cons
    await prod

    check_prefix(pushed, popped)
    assert len(popped) == N, f"only {len(popped)}/{N} drained"
    dut._log.info(f"async_fifo: {len(pushed)} pushed / {len(popped)} popped, "
                  f"order + integrity OK across 5ns:11ns clocks")
