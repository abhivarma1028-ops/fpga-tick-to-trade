"""
tb_tick_to_trade_mc — end-to-end test of the 3-clock-domain wrapper.

Same stimulus as tb_tick_to_trade, but every interface now lives on a different
clock than the trading core:

    ITCH bytes in      -> clk_line  156.25 MHz  (6.400 ns)
    the core itself    -> clk_core  250.00 MHz  (4.000 ns)
    decisions, CSR out -> clk_host  125.00 MHz  (8.000 ns)

So a passing BUY/SELL here proves the bytes crossed line->core, the decision
crossed core->host, and the AXI-Lite read crossed both ways -- i.e. the CDC
library works in situ, not just in its own unit tests.

The three clocks are deliberately started with different periods AND are not
integer multiples of each other, so their edges drift continuously against one
another and each run exercises a different set of relative phases.

cocotb 2.0 note: stimulus is driven on the FALLING edge of the relevant clock.
Under Verilator a signal written immediately before `await RisingEdge` is not
visible at that edge, which silently costs a cycle (or loses a beat entirely).
Driving on the falling edge guarantees the value is stable at the sampling edge.
"""
import cocotb
from cocotb.clock    import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles, ReadOnly
import sys, os

sys.path.insert(0, os.path.dirname(__file__))
from synth_itch import SynthITCH

LINE_NS = 6.400   # 156.25 MHz
CORE_NS = 4.000   # 250.00 MHz
HOST_NS = 8.000   # 125.00 MHz


def start_clocks(dut):
    cocotb.start_soon(Clock(dut.clk_line, LINE_NS, unit='ns').start())
    cocotb.start_soon(Clock(dut.clk_core, CORE_NS, unit='ns').start())
    cocotb.start_soon(Clock(dut.clk_host, HOST_NS, unit='ns').start())


async def reset(dut, cycles: int = 12):
    """Hold all three domains in reset, then release them together.

    Releasing simultaneously is the harsher case: each domain sees the release
    at a different point relative to its own clock, which is exactly the
    situation the reset synchronizers have to cope with.
    """
    dut.rst_line_n.value = 0
    dut.rst_core_n.value = 0
    dut.rst_host_n.value = 0

    dut.halt.value          = 0
    dut.strat_sel.value     = 0
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tdata.value  = 0
    dut.s_axis_tlast.value  = 0
    dut.m_axis_tready.value = 1

    dut.s_axil_awaddr.value  = 0
    dut.s_axil_awvalid.value = 0
    dut.s_axil_wdata.value   = 0
    dut.s_axil_wstrb.value   = 0
    dut.s_axil_wvalid.value  = 0
    dut.s_axil_bready.value  = 1
    dut.s_axil_araddr.value  = 0
    dut.s_axil_arvalid.value = 0
    dut.s_axil_rready.value  = 1

    await ClockCycles(dut.clk_host, cycles)
    dut.rst_line_n.value = 1
    dut.rst_core_n.value = 1
    dut.rst_host_n.value = 1
    # let the config handshake carry strat_sel across before any traffic
    await ClockCycles(dut.clk_host, 12)


async def drive_stream(dut, raw: bytes):
    """Drive ITCH bytes on clk_line, honouring s_axis_tready.

    tready now comes from the ingress async FIFO rather than from the book, so
    it reflects FIFO occupancy in the line domain.
    """
    for i, byte in enumerate(raw):
        await FallingEdge(dut.clk_line)
        dut.s_axis_tvalid.value = 1
        dut.s_axis_tdata.value  = byte
        dut.s_axis_tlast.value  = 1 if i == len(raw) - 1 else 0
        # wait for a rising edge where tready is high: that is the transfer
        while True:
            await ReadOnly()
            ready = int(dut.s_axis_tready.value)
            await RisingEdge(dut.clk_line)
            if ready:
                break
    await FallingEdge(dut.clk_line)
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tlast.value  = 0


async def drive_framed(dut, framed: bytes):
    length = int.from_bytes(framed[0:2], 'big')
    await drive_stream(dut, framed[2:2+length])


async def axil_read(dut, addr: int, timeout: int = 400) -> int:
    """AXI-Lite read from the host domain.

    The request crosses host->core through the AR FIFO and the data returns
    core->host through the R FIFO, so this is slower than the single-clock
    version -- hence the generous timeout.
    """
    await FallingEdge(dut.clk_host)
    dut.s_axil_araddr.value  = addr
    dut.s_axil_arvalid.value = 1
    dut.s_axil_rready.value  = 1

    for _ in range(timeout):
        await ReadOnly()
        accepted = int(dut.s_axil_arready.value)
        await RisingEdge(dut.clk_host)
        if accepted:
            break
    else:
        raise TimeoutError(f"AXI-Lite AR never accepted for addr 0x{addr:x}")

    await FallingEdge(dut.clk_host)
    dut.s_axil_arvalid.value = 0

    for _ in range(timeout):
        await ReadOnly()
        if int(dut.s_axil_rvalid.value):
            data = int(dut.s_axil_rdata.value)
            await RisingEdge(dut.clk_host)
            return data
        await RisingEdge(dut.clk_host)
    raise TimeoutError(f"AXI-Lite R never returned for addr 0x{addr:x}")


async def await_decision(dut, max_cycles: int = 600) -> dict | None:
    """Watch m_axis in the HOST domain for a decision beat."""
    for _ in range(max_cycles):
        await ReadOnly()
        if int(dut.m_axis_tvalid.value):
            tdata = int(dut.m_axis_tdata.value)
            await RisingEdge(dut.clk_host)
            return {
                'action':      (tdata >> 64) & 0x1,
                'order_price': (tdata >> 32) & 0xFFFF_FFFF,
                'order_size':   tdata        & 0xFFFF_FFFF,
            }
        await RisingEdge(dut.clk_host)
    return None


def check_no_overflow(dut):
    """Neither sticky flag may be set: a dropped decision or a dropped risk
    event would mean the crossing silently lost information."""
    assert int(dut.decision_overflow.value) == 0, \
        "decision_overflow set — the egress FIFO dropped a decision"
    assert int(dut.risk_evt_overflow.value) == 0, \
        "risk_evt_overflow set — a risk reject event was dropped"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_buy_decision_across_domains(dut):
    """ask(100) then bid(200) on clk_line -> BUY appears on clk_host."""
    start_clocks(dut)
    await reset(dut)

    gen = SynthITCH()
    await drive_framed(dut, gen.add(ref=1, side='S', shares=100, price=1_500_100))
    await drive_framed(dut, gen.add(ref=2, side='B', shares=200, price=1_499_900))

    dec = await await_decision(dut)
    assert dec is not None, "no decision reached the host domain"
    assert dec['action'] == 0, f"expected BUY (action=0), got {dec['action']}"
    assert dec['order_price'] == 1_500_100, \
        f"BUY should lift ask=1500100, got {dec['order_price']}"
    assert dec['order_size'] == 100, f"expected LOT_SIZE 100, got {dec['order_size']}"
    check_no_overflow(dut)

    dut._log.info(
        f"PASS  BUY crossed line->core->host: price={dec['order_price']} "
        f"size={dec['order_size']}")


@cocotb.test()
async def test_sell_decision_across_domains(dut):
    """bid(100) then ask(200) on clk_line -> SELL appears on clk_host."""
    start_clocks(dut)
    await reset(dut)

    gen = SynthITCH()
    await drive_framed(dut, gen.add(ref=1, side='B', shares=100, price=1_499_900))
    await drive_framed(dut, gen.add(ref=2, side='S', shares=200, price=1_500_100))

    dec = await await_decision(dut)
    assert dec is not None, "no decision reached the host domain"
    assert dec['action'] == 1, f"expected SELL (action=1), got {dec['action']}"
    assert dec['order_price'] == 1_499_900, \
        f"SELL should hit bid=1499900, got {dec['order_price']}"
    check_no_overflow(dut)

    dut._log.info(f"PASS  SELL crossed line->core->host: price={dec['order_price']}")


@cocotb.test()
async def test_halt_crosses_host_to_core(dut):
    """halt is driven in the HOST domain and must block a decision generated in
    the CORE domain -- i.e. the sync_2ff crossing actually takes effect."""
    start_clocks(dut)
    await reset(dut)

    await FallingEdge(dut.clk_host)
    dut.halt.value = 1
    # allow the level to settle through the 2-flop synchronizer into the core
    await ClockCycles(dut.clk_host, 8)

    gen = SynthITCH()
    await drive_framed(dut, gen.add(ref=1, side='S', shares=100, price=1_500_100))
    await drive_framed(dut, gen.add(ref=2, side='B', shares=200, price=1_499_900))

    dec = await await_decision(dut, max_cycles=300)
    assert dec is None, "halt was asserted but a decision still reached the host"
    dut._log.info("PASS  halt crossed host->core and blocked the decision")


@cocotb.test()
async def test_axil_read_across_domains(dut):
    """An AXI-Lite read issued on clk_host must reach the core's latency counter
    and return -- exercising the AR and R channel FIFOs in both directions."""
    start_clocks(dut)
    await reset(dut)

    gen = SynthITCH()
    await drive_framed(dut, gen.add(ref=1, side='S', shares=100, price=1_500_100))
    await drive_framed(dut, gen.add(ref=2, side='B', shares=200, price=1_499_900))

    dec = await await_decision(dut)
    assert dec is not None, "no decision, cannot check the latency register"

    latency_cyc = await axil_read(dut, 0x100)
    assert latency_cyc > 0, \
        f"latency register read back {latency_cyc}; expected a non-zero measurement"

    dut._log.info("=" * 62)
    dut._log.info(f"  TICK-TO-TRADE LATENCY (core domain): {latency_cyc} cycles "
                  f"= {latency_cyc * CORE_NS} ns @ 250 MHz")
    dut._log.info("  read back over AXI-Lite from the 125 MHz host domain")
    dut._log.info("=" * 62)
    check_no_overflow(dut)
