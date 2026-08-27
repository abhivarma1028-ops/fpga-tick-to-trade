"""
cocotb: rtl/cdc/handshake_mcp.sv — atomic multi-bit config crossing.

The source offers random 32-bit words, accepting one only when `src_ready`; a
destination monitor records `dst_data` on each `dst_valid` pulse. Pass condition:
every accepted word appears at the destination exactly once, in order, never
torn — i.e. captured == accepted after the last word drains. Run both clock
directions.

Run: cd sim && make cdc-run TOPLEVEL=handshake_mcp MODULE=tb_handshake_mcp
"""
import os, sys, random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, Timer

sys.path.insert(0, os.path.dirname(__file__))
from golden.handshake_mcp import check_prefix

N = 40
MASK = 0xFFFFFFFF


async def _reset(dut):
    dut.src_rst_n.value = 0
    dut.dst_rst_n.value = 0
    dut.src_valid.value = 0
    dut.src_data.value = 0
    await Timer(60, unit="ns")
    dut.src_rst_n.value = 1
    dut.dst_rst_n.value = 1
    await RisingEdge(dut.src_clk)
    await RisingEdge(dut.dst_clk)


async def _dst_monitor(dut, captured):
    # sample mid-cycle so the one-cycle dst_valid strobe is caught reliably
    while True:
        await FallingEdge(dut.dst_clk)
        if int(dut.dst_valid.value):
            captured.append(int(dut.dst_data.value))


async def _run(dut, src_ns, dst_ns, seed):
    cocotb.start_soon(Clock(dut.src_clk, src_ns, unit="ns").start())
    cocotb.start_soon(Clock(dut.dst_clk, dst_ns, unit="ns").start())
    await _reset(dut)

    captured = []
    cocotb.start_soon(_dst_monitor(dut, captured))
    rng = random.Random(seed)

    # Drive on the falling edge so values are stable at the sampling rising edge
    # (cocotb+Verilator applies writes with a one-edge visibility delay).
    accepted = []
    for _ in range(N):
        val = rng.randint(0, MASK)
        await FallingEdge(dut.src_clk)
        while int(dut.src_ready.value) == 0:
            await FallingEdge(dut.src_clk)
        dut.src_data.value = val
        dut.src_valid.value = 1
        await RisingEdge(dut.src_clk)       # accepted here (ready was high)
        dut.src_valid.value = 0
        await FallingEdge(dut.src_clk)      # clear valid well before ready returns
        accepted.append(val)
        for _ in range(rng.randint(0, 3)):  # random gap
            await FallingEdge(dut.src_clk)

    for _ in range(40):                     # flush the last word across
        await RisingEdge(dut.dst_clk)

    check_prefix(accepted, captured)
    assert len(captured) == N, f"captured {len(captured)}/{N} words"
    dut._log.info(f"handshake_mcp: {N} config words crossed atomically "
                  f"(src={src_ns}ns dst={dst_ns}ns)")


@cocotb.test()
async def fast_to_slow(dut):
    await _run(dut, src_ns=5, dst_ns=9, seed=8)


@cocotb.test()
async def slow_to_fast(dut):
    await _run(dut, src_ns=9, dst_ns=5, seed=9)
