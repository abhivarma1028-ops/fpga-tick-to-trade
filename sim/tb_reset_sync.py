"""
tb_reset_sync — asynchronous assert, synchronous release.

Two properties, and they are the whole point of the block:

  1. ASSERT is asynchronous. Dropping arst_n must pull rst_n low immediately,
     without waiting for a clock edge -- and it must work with the clock stopped
     entirely, which is the case a purely synchronous reset cannot handle.

  2. RELEASE is synchronous. Raising arst_n must NOT release rst_n immediately.
     It must take STAGES clock edges, so every flop in the domain leaves reset
     on the same edge instead of some seeing the release and some not.

Test 3 checks the release is genuinely edge-driven rather than just delayed: with
the clock stopped, rst_n must stay asserted no matter how long arst_n has been
high.

cocotb 2.0 note: stimulus is driven on the falling edge. Under Verilator a value
written immediately before `await RisingEdge` is not visible at that edge.
"""
import cocotb
from cocotb.clock    import Clock
from cocotb.triggers import RisingEdge, FallingEdge, Timer, ClockCycles

CLK_NS = 4.0
STAGES = 3          # must match the RTL default


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit='ns').start())
    dut.arst_n.value = 1
    await ClockCycles(dut.clk, 5)


@cocotb.test()
async def test_async_assert(dut):
    """Dropping arst_n asserts rst_n immediately, between clock edges."""
    await start(dut)
    # let the chain fill so rst_n is high to begin with
    await ClockCycles(dut.clk, STAGES + 2)
    assert int(dut.rst_n.value) == 1, "rst_n should have released before this test"

    # drop the reset deliberately OFF a clock edge
    await RisingEdge(dut.clk)
    await Timer(CLK_NS / 2, unit='ns')
    dut.arst_n.value = 0
    await Timer(1, unit='ns')          # no clock edge has occurred here

    assert int(dut.rst_n.value) == 0, \
        "rst_n did not assert until a clock edge -- the assert path is not asynchronous"
    dut._log.info("PASS  assert is asynchronous (no clock edge required)")


@cocotb.test()
async def test_sync_release_takes_stages_edges(dut):
    """Releasing arst_n must take exactly STAGES clock edges, not effect immediately."""
    await start(dut)
    dut.arst_n.value = 0
    await ClockCycles(dut.clk, 3)
    assert int(dut.rst_n.value) == 0, "rst_n should be asserted while arst_n is low"

    # release on a falling edge so it is stable well before the next rising edge
    await FallingEdge(dut.clk)
    dut.arst_n.value = 1
    await Timer(1, unit='ns')
    assert int(dut.rst_n.value) == 0, \
        "rst_n released the instant arst_n rose -- release is not synchronous"

    # count the edges until it releases
    edges = 0
    for _ in range(STAGES + 4):
        await RisingEdge(dut.clk)
        await Timer(0.1, unit='ns')
        edges += 1
        if int(dut.rst_n.value) == 1:
            break

    assert int(dut.rst_n.value) == 1, f"rst_n never released within {STAGES + 4} edges"
    assert edges == STAGES, \
        f"release took {edges} clock edges, expected exactly STAGES={STAGES}"
    dut._log.info(f"PASS  release is synchronous and took exactly {edges} edges")


@cocotb.test()
async def test_no_release_without_clock(dut):
    """With the clock stopped, rst_n must stay asserted however long arst_n is high.

    This separates a genuinely edge-driven release from one that merely looks
    delayed in simulation.
    """
    # deliberately no clock for this test
    dut.arst_n.value = 0
    await Timer(20, unit='ns')
    assert int(dut.rst_n.value) == 0, "rst_n should assert with no clock running"

    dut.arst_n.value = 1
    await Timer(200, unit='ns')        # 50 clock periods' worth of dead time
    assert int(dut.rst_n.value) == 0, \
        "rst_n released with the clock stopped -- the release is not edge-driven"
    dut._log.info("PASS  no release without clock edges")
