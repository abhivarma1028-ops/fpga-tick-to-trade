"""
cocotb: rtl/cdc/axis_async_fifo.sv — AXI-Stream clock-crossing FIFO.

Independent slave (producer) and master (consumer) clocks. Standard AXIS
ready/valid with random throttling on both ends. Checks that the {tdata, tlast}
beat sequence crosses in order with no loss/dup and framing (tlast) preserved.

Run: cd sim && make cdc-run TOPLEVEL=axis_async_fifo MODULE=tb_axis_async_fifo
"""
import os, sys, random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, Timer

sys.path.insert(0, os.path.dirname(__file__))
from golden.async_fifo import check_prefix

N = 200            # 0..199 fit 8-bit tdata
FRAME = 8          # tlast every FRAME beats


async def _reset(dut):
    dut.s_rst_n.value = 0
    dut.m_rst_n.value = 0
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tdata.value = 0
    dut.s_axis_tlast.value = 0
    dut.m_axis_tready.value = 0
    await Timer(60, unit="ns")
    dut.s_rst_n.value = 1
    dut.m_rst_n.value = 1
    await RisingEdge(dut.s_clk)
    await RisingEdge(dut.m_clk)


async def _producer(dut, sent, seed):
    rng = random.Random(seed)
    i = 0
    while i < N:
        await FallingEdge(dut.s_clk)
        if int(dut.s_axis_tready.value) == 1 and rng.random() < 0.75:
            last = 1 if ((i % FRAME) == (FRAME - 1)) else 0
            dut.s_axis_tdata.value = i
            dut.s_axis_tlast.value = last
            dut.s_axis_tvalid.value = 1
            sent.append((i, last))
            i += 1
        else:
            dut.s_axis_tvalid.value = 0
    await RisingEdge(dut.s_clk)      # let the final asserted beat transfer
    dut.s_axis_tvalid.value = 0


async def _consumer(dut, got, seed):
    rng = random.Random(seed)
    guard = 0
    dut.m_axis_tready.value = 0
    while len(got) < N:
        await FallingEdge(dut.m_clk)
        guard += 1
        assert guard < 200_000, "consumer stalled"
        # FWFT: when a beat is present and we choose to accept, record the head
        # now and assert tready so it transfers at the next rising edge.
        if int(dut.m_axis_tvalid.value) == 1 and rng.random() < 0.5:
            got.append((int(dut.m_axis_tdata.value), int(dut.m_axis_tlast.value)))
            dut.m_axis_tready.value = 1
        else:
            dut.m_axis_tready.value = 0
    dut.m_axis_tready.value = 0


@cocotb.test()
async def axis_async_fifo_stream(dut):
    cocotb.start_soon(Clock(dut.s_clk, 7, unit="ns").start())    # slow producer
    cocotb.start_soon(Clock(dut.m_clk, 5, unit="ns").start())    # fast consumer
    await _reset(dut)

    sent, got = [], []
    prod = cocotb.start_soon(_producer(dut, sent, seed=6))
    cons = cocotb.start_soon(_consumer(dut, got, seed=7))
    await cons
    await prod

    check_prefix(sent, got)
    assert len(got) == N, f"only {len(got)}/{N} beats received"
    # framing preserved: tlast positions match
    exp_last = [b for b in range(N) if (b % FRAME) == (FRAME - 1)]
    got_last = [b for b, (d, l) in enumerate(got) if l]
    assert got_last == exp_last, "tlast framing not preserved across the crossing"
    dut._log.info(f"axis_async_fifo: {len(got)} beats, {len(got_last)} frames, "
                  f"order + framing OK across 7ns:5ns clocks")
