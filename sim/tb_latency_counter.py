"""
Testbench for latency_counter.sv — AXI-Lite M2 interface.

Tests:
  1. Single measurement: msg_start → decision_valid, read last_latency and hist[bucket]
  2. Multiple measurements accumulate in histogram
  3. Clear register resets histogram and last_latency
  4. AXI-Lite read of undefined address returns 0xDEADBEEF
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles


# ---------------------------------------------------------------------------
# AXI-Lite helpers
# ---------------------------------------------------------------------------

async def axil_read(dut, addr: int) -> int:
    """Issue a single AXI-Lite read and return the 32-bit data."""
    await RisingEdge(dut.clk)
    dut.s_axil_araddr.value  = addr
    dut.s_axil_arvalid.value = 1
    dut.s_axil_rready.value  = 1

    # Wait for arready
    while not dut.s_axil_arready.value:
        await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.s_axil_arvalid.value = 0

    # Wait for rvalid
    while not dut.s_axil_rvalid.value:
        await RisingEdge(dut.clk)

    data = int(dut.s_axil_rdata.value)
    await RisingEdge(dut.clk)
    dut.s_axil_rready.value = 0
    return data


async def axil_write(dut, addr: int, data: int):
    """Issue a single AXI-Lite write (address and data presented together)."""
    await RisingEdge(dut.clk)
    dut.s_axil_awaddr.value  = addr
    dut.s_axil_awvalid.value = 1
    dut.s_axil_wdata.value   = data
    dut.s_axil_wstrb.value   = 0xF
    dut.s_axil_wvalid.value  = 1
    dut.s_axil_bready.value  = 1

    # Wait for awready
    while not dut.s_axil_awready.value:
        await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.s_axil_awvalid.value = 0

    # Wait for wready
    while not dut.s_axil_wready.value:
        await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.s_axil_wvalid.value = 0

    # Wait for bvalid
    while not dut.s_axil_bvalid.value:
        await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.s_axil_bready.value = 0


async def reset(dut):
    dut.rst_n.value          = 0
    dut.msg_start.value      = 0
    dut.decision_valid.value = 0
    dut.s_axil_awaddr.value  = 0
    dut.s_axil_awvalid.value = 0
    dut.s_axil_wdata.value   = 0
    dut.s_axil_wstrb.value   = 0
    dut.s_axil_wvalid.value  = 0
    dut.s_axil_bready.value  = 0
    dut.s_axil_araddr.value  = 0
    dut.s_axil_arvalid.value = 0
    dut.s_axil_rready.value  = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


async def measure(dut, gap_cycles: int):
    """Pulse msg_start, wait gap_cycles, pulse decision_valid."""
    dut.msg_start.value = 1
    await RisingEdge(dut.clk)
    dut.msg_start.value = 0
    await ClockCycles(dut.clk, gap_cycles - 1)
    dut.decision_valid.value = 1
    await RisingEdge(dut.clk)
    dut.decision_valid.value = 0
    await ClockCycles(dut.clk, 2)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_single_measurement(dut):
    """msg_start → N cycles → decision_valid; verify last_latency and hist[N]."""
    cocotb.start_soon(Clock(dut.clk, 5, unit="ns").start())
    await reset(dut)

    GAP = 10
    await measure(dut, GAP)

    lat = await axil_read(dut, 0x100)
    assert lat == GAP, f"last_latency expected {GAP}, got {lat}"

    hist_val = await axil_read(dut, GAP * 4)  # hist[GAP] at addr GAP*4
    assert hist_val == 1, f"hist[{GAP}] expected 1, got {hist_val}"

    dut._log.info("PASS test_single_measurement: latency=%d cycles", lat)


@cocotb.test()
async def test_histogram_accumulation(dut):
    """Fire 5 measurements at same gap; histogram bucket must show count=5."""
    cocotb.start_soon(Clock(dut.clk, 5, unit="ns").start())
    await reset(dut)

    GAP = 15
    REPS = 5
    for _ in range(REPS):
        await measure(dut, GAP)

    hist_val = await axil_read(dut, GAP * 4)
    assert hist_val == REPS, f"hist[{GAP}] expected {REPS}, got {hist_val}"

    dut._log.info("PASS test_histogram_accumulation: hist[%d]=%d", GAP, hist_val)


@cocotb.test()
async def test_clear_register(dut):
    """Write 1 to 0x104; histogram and last_latency must reset to 0."""
    cocotb.start_soon(Clock(dut.clk, 5, unit="ns").start())
    await reset(dut)

    await measure(dut, 20)

    lat_before = await axil_read(dut, 0x100)
    assert lat_before == 20

    # Clear
    await axil_write(dut, 0x104, 1)

    lat_after = await axil_read(dut, 0x100)
    assert lat_after == 0, f"last_latency after clear expected 0, got {lat_after}"

    hist_after = await axil_read(dut, 20 * 4)
    assert hist_after == 0, f"hist[20] after clear expected 0, got {hist_after}"

    dut._log.info("PASS test_clear_register")


@cocotb.test()
async def test_bucket_saturation(dut):
    """Delta >= 64 must saturate into bucket 63."""
    cocotb.start_soon(Clock(dut.clk, 5, unit="ns").start())
    await reset(dut)

    await measure(dut, 100)  # 100 > 63, saturates to bucket 63

    hist_63 = await axil_read(dut, 63 * 4)
    assert hist_63 == 1, f"hist[63] (saturated) expected 1, got {hist_63}"

    dut._log.info("PASS test_bucket_saturation")


@cocotb.test()
async def test_undefined_address(dut):
    """Read of unmapped address must return 0xDEADBEEF."""
    cocotb.start_soon(Clock(dut.clk, 5, unit="ns").start())
    await reset(dut)

    val = await axil_read(dut, 0x1F0)
    assert val == 0xDEADBEEF, f"Undefined addr expected 0xDEADBEEF, got 0x{val:08X}"

    dut._log.info("PASS test_undefined_address")
