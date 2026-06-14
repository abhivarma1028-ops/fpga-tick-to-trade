"""
cocotb testbench for itch_parser.sv
Diffs RTL decoded output against the Python golden model byte-for-byte.

Run:
    cd sim && make SIM=questa COCOTB_TOPLEVEL=itch_parser COCOTB_TEST_MODULES=tb_itch_parser
"""

import cocotb
from cocotb.clock    import Clock
from cocotb.triggers import RisingEdge, ClockCycles
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from synth_itch         import SynthITCH
from golden.itch_parser import parse_stream, MsgType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def reset(dut, cycles: int = 5):
    dut.rst_n.value           = 0
    dut.s_axis_tvalid.value   = 0
    dut.s_axis_tdata.value    = 0
    dut.s_axis_tlast.value    = 0
    dut.m_ready.value         = 1   # downstream (book) ready by default
    await ClockCycles(dut.clk, cycles)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def drive_message(dut, raw: bytes):
    """Drive one framing-stripped ITCH message byte-by-byte onto AXI-Stream."""
    for i, byte in enumerate(raw):
        dut.s_axis_tvalid.value = 1
        dut.s_axis_tdata.value  = byte
        dut.s_axis_tlast.value  = 1 if i == len(raw) - 1 else 0
        await RisingEdge(dut.clk)
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tlast.value  = 0


def _snapshot(dut) -> dict:
    return {
        'msg_type':      int(dut.msg_type.value),
        'timestamp':     int(dut.timestamp.value),
        'order_ref':     int(dut.order_ref.value),
        'new_order_ref': int(dut.new_order_ref.value),
        'side':          int(dut.side.value),
        'shares':        int(dut.shares.value),
        'price':         int(dut.price.value),
    }


async def collect_output(dut, timeout_cycles: int = 10) -> dict:
    """Wait for m_valid to pulse and return captured fields.
    Checks the CURRENT simulation time first (m_valid may already be high
    right at the clock edge where drive_message returned) before advancing.
    """
    if dut.m_valid.value:
        return _snapshot(dut)
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        if dut.m_valid.value:
            return _snapshot(dut)
    raise TimeoutError("m_valid never asserted within timeout")


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_add_order(dut):
    """Single Add Order: verify all fields match golden model."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen    = SynthITCH()
    stream = gen.add(ref=42, side='B', shares=500, price=1234567)
    length = int.from_bytes(stream[0:2], 'big')
    raw    = stream[2:2+length]
    g      = parse_stream(stream)[0]

    await drive_message(dut, raw)
    out = await collect_output(dut)

    assert out['msg_type']  == g.msg_type,  f"msg_type:  {out['msg_type']:#x} != {g.msg_type:#x}"
    assert out['timestamp'] == g.timestamp, f"timestamp: {out['timestamp']} != {g.timestamp}"
    assert out['order_ref'] == g.order_ref, f"order_ref: {out['order_ref']} != {g.order_ref}"
    assert out['side']      == g.side,      f"side:      {out['side']} != {g.side}"
    assert out['shares']    == g.shares,    f"shares:    {out['shares']} != {g.shares}"
    assert out['price']     == g.price,     f"price:     {out['price']} != {g.price}"

    dut._log.info(f"PASS  Add Order ref={g.order_ref} price={g.price/10000:.4f} shares={g.shares}")


@cocotb.test()
async def test_sell_add(dut):
    """Add Order sell side."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen    = SynthITCH()
    stream = gen.add(ref=99, side='S', shares=200, price=9_990_000)
    raw    = stream[2:2+int.from_bytes(stream[0:2], 'big')]
    g      = parse_stream(stream)[0]

    await drive_message(dut, raw)
    out = await collect_output(dut)

    assert out['side']      == 1,      "sell side should be 1"
    assert out['side']      == g.side
    assert out['order_ref'] == g.order_ref
    assert out['shares']    == g.shares
    assert out['price']     == g.price
    dut._log.info("PASS  Sell Add Order")


@cocotb.test()
async def test_cancel(dut):
    """Cancel: check order_ref and shares; price should be 0."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen    = SynthITCH()
    stream = gen.cancel(ref=7, shares=100)
    raw    = stream[2:2+int.from_bytes(stream[0:2], 'big')]
    g      = parse_stream(stream)[0]

    await drive_message(dut, raw)
    out = await collect_output(dut)

    assert out['msg_type']  == MsgType.CANCEL, f"msg_type: {out['msg_type']:#x}"
    assert out['order_ref'] == g.order_ref,    f"order_ref: {out['order_ref']} != {g.order_ref}"
    assert out['shares']    == g.shares,       f"shares: {out['shares']} != {g.shares}"
    assert out['price']     == 0
    dut._log.info("PASS  Cancel message")


@cocotb.test()
async def test_delete(dut):
    """Delete: verify order_ref captured correctly (last byte coincides with tlast)."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen    = SynthITCH()
    stream = gen.delete(ref=13)
    raw    = stream[2:2+int.from_bytes(stream[0:2], 'big')]
    g      = parse_stream(stream)[0]

    await drive_message(dut, raw)
    out = await collect_output(dut)

    assert out['msg_type']  == MsgType.DELETE, f"msg_type: {out['msg_type']:#x}"
    assert out['order_ref'] == g.order_ref,    f"order_ref: {out['order_ref']} != {g.order_ref}"
    dut._log.info("PASS  Delete message")


@cocotb.test()
async def test_execute(dut):
    """Execute message."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen    = SynthITCH()
    stream = gen.execute(ref=55, shares=75)
    raw    = stream[2:2+int.from_bytes(stream[0:2], 'big')]
    g      = parse_stream(stream)[0]

    await drive_message(dut, raw)
    out = await collect_output(dut)

    assert out['msg_type']  == MsgType.EXECUTE, f"msg_type: {out['msg_type']:#x}"
    assert out['order_ref'] == g.order_ref,     f"order_ref: {out['order_ref']} != {g.order_ref}"
    assert out['shares']    == g.shares,        f"shares: {out['shares']} != {g.shares}"
    dut._log.info("PASS  Execute message")


@cocotb.test()
async def test_add_mpid(dut):
    """Add Order with MPID (F): 40-byte message; parsed exactly like an Add."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen    = SynthITCH()
    stream = gen.add_mpid(ref=77, side='B', shares=350, price=1_555_000)
    raw    = stream[2:2+int.from_bytes(stream[0:2], 'big')]
    g      = parse_stream(stream)[0]

    await drive_message(dut, raw)
    out = await collect_output(dut)

    assert out['msg_type']  == MsgType.ADD_MPID, f"msg_type: {out['msg_type']:#x}"
    assert out['order_ref'] == g.order_ref
    assert out['side']      == g.side
    assert out['shares']    == g.shares,         f"shares: {out['shares']} != {g.shares}"
    assert out['price']     == g.price,          f"price: {out['price']} != {g.price}"
    dut._log.info(f"PASS  Add+MPID ref={g.order_ref} price={g.price/10000:.4f}")


@cocotb.test()
async def test_execute_with_price(dut):
    """Execute-with-Price (C): exec shares plus execution price."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen    = SynthITCH()
    stream = gen.execute_with_price(ref=88, shares=120, price=1_499_500)
    raw    = stream[2:2+int.from_bytes(stream[0:2], 'big')]
    g      = parse_stream(stream)[0]

    await drive_message(dut, raw)
    out = await collect_output(dut)

    assert out['msg_type']  == MsgType.EXEC_PRICE, f"msg_type: {out['msg_type']:#x}"
    assert out['order_ref'] == g.order_ref
    assert out['shares']    == g.shares, f"shares: {out['shares']} != {g.shares}"
    assert out['price']     == g.price,  f"price: {out['price']} != {g.price}"
    dut._log.info(f"PASS  Execute-with-Price ref={g.order_ref} px={g.price/10000:.4f}")


@cocotb.test()
async def test_replace(dut):
    """Replace (U): two order refs, shifted shares/price offsets."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen    = SynthITCH()
    stream = gen.replace(orig_ref=10, new_ref=20, shares=250, price=1_600_000)
    raw    = stream[2:2+int.from_bytes(stream[0:2], 'big')]
    g      = parse_stream(stream)[0]

    await drive_message(dut, raw)
    out = await collect_output(dut)

    assert out['msg_type']      == MsgType.REPLACE,   f"msg_type: {out['msg_type']:#x}"
    assert out['order_ref']     == g.order_ref,       f"orig_ref: {out['order_ref']} != {g.order_ref}"
    assert out['new_order_ref'] == g.new_order_ref,   f"new_ref: {out['new_order_ref']} != {g.new_order_ref}"
    assert out['shares']        == g.shares,          f"shares: {out['shares']} != {g.shares}"
    assert out['price']         == g.price,           f"price: {out['price']} != {g.price}"
    dut._log.info(f"PASS  Replace orig={g.order_ref} new={g.new_order_ref} shares={g.shares}")


@cocotb.test()
async def test_trade(dut):
    """Trade print (P): 44-byte message; side/shares/price decoded like Add."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen    = SynthITCH()
    stream = gen.trade(ref=909, side='S', shares=250, price=1_502_500)
    raw    = stream[2:2+int.from_bytes(stream[0:2], 'big')]
    g      = parse_stream(stream)[0]

    await drive_message(dut, raw)
    out = await collect_output(dut)

    assert out['msg_type']  == MsgType.TRADE, f"msg_type: {out['msg_type']:#x}"
    assert out['order_ref'] == g.order_ref
    assert out['side']      == g.side,   f"side: {out['side']} != {g.side}"
    assert out['shares']    == g.shares, f"shares: {out['shares']} != {g.shares}"
    assert out['price']     == g.price,  f"price: {out['price']} != {g.price}"
    dut._log.info(f"PASS  Trade print ref={g.order_ref} px={g.price/10000:.4f}")


@cocotb.test()
async def test_backpressure_holds_output(dut):
    """With m_ready held low, a decoded message must be HELD (m_valid stays high,
    fields stable) and the byte input must stall (s_axis_tready low) — not dropped.
    Asserting m_ready then completes the handshake and clears m_valid."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen    = SynthITCH()
    stream = gen.add(ref=321, side='B', shares=400, price=1_234_000)
    raw    = stream[2:2+int.from_bytes(stream[0:2], 'big')]
    g      = parse_stream(stream)[0]

    # Downstream NOT ready
    dut.m_ready.value = 0
    await drive_message(dut, raw)

    # Find the cycle m_valid asserts (held, since m_ready=0)
    for _ in range(5):
        if int(dut.m_valid.value):
            break
        await RisingEdge(dut.clk)
    assert int(dut.m_valid.value) == 1, "m_valid should assert after a full message"

    # Held across several idle cycles: valid stays high, tready stalls input
    for _ in range(5):
        await RisingEdge(dut.clk)
        assert int(dut.m_valid.value) == 1,        "m_valid must HOLD while !m_ready"
        assert int(dut.s_axis_tready.value) == 0,  "input must stall while holding output"
        assert int(dut.order_ref.value) == g.order_ref, "held fields must stay stable"

    # Accept it
    dut.m_ready.value = 1
    await RisingEdge(dut.clk)   # accept handshake (m_valid & m_ready)
    await RisingEdge(dut.clk)   # m_valid cleared
    assert int(dut.m_valid.value) == 0,       "m_valid must clear after accept"
    assert int(dut.s_axis_tready.value) == 1, "input resumes after accept"
    dut._log.info("PASS  backpressure: output held + input stalled until m_ready")


@cocotb.test()
async def test_back_to_back(dut):
    """Stream three messages back-to-back with no inter-message gap.
    Uses a parallel monitor coroutine so m_valid pulses are never missed.
    """
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen = SynthITCH()
    messages_framed = [
        gen.add(ref=1,  side='B', shares=100, price=1000000),
        gen.add(ref=2,  side='S', shares=200, price=1001000),
        gen.cancel(ref=1, shares=50),
    ]
    golden = []
    for framed in messages_framed:
        golden += parse_stream(framed)

    # Collect m_valid pulses in a background coroutine so none are missed
    captured = []
    async def monitor():
        while len(captured) < len(messages_framed):
            await RisingEdge(dut.clk)
            if dut.m_valid.value:
                captured.append(_snapshot(dut))

    mon = cocotb.start_soon(monitor())

    # Drive messages back-to-back with no gap
    for framed in messages_framed:
        raw = framed[2:2+int.from_bytes(framed[0:2], 'big')]
        await drive_message(dut, raw)

    # Give monitor a few extra cycles to catch the last pulse
    await ClockCycles(dut.clk, 5)
    await mon

    assert len(captured) == len(golden), \
        f"expected {len(golden)} outputs, got {len(captured)}"

    for i, (out, g) in enumerate(zip(captured, golden)):
        assert out['msg_type']  == g.msg_type,  f"msg {i} type mismatch"
        assert out['order_ref'] == g.order_ref, f"msg {i} order_ref mismatch"
        if g.msg_type == MsgType.ADD:
            assert out['shares'] == g.shares, f"msg {i} shares mismatch"
            assert out['price']  == g.price,  f"msg {i} price mismatch"

    dut._log.info("PASS  Back-to-back stream (3 messages)")
