"""
cocotb testbench for order_book_top.sv
Drives decoded messages directly onto the DUT and diffs top-of-book outputs
against the Python golden model at every step.

Run:
    cd sim && make SIM=questa TOPLEVEL=order_book_top MODULE=tb_order_book
"""

import cocotb
from cocotb.clock    import Clock
from cocotb.triggers import RisingEdge, ClockCycles
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from synth_itch         import SynthITCH
from golden.itch_parser import parse_stream, ParsedMsg, MsgType
from golden.order_book  import OrderBook


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def reset(dut, cycles: int = 5):
    dut.rst_n.value     = 0
    dut.msg_valid.value = 0
    dut.msg_type.value  = 0
    dut.order_ref.value = 0
    dut.side.value      = 0
    dut.shares.value    = 0
    dut.price.value     = 0
    await ClockCycles(dut.clk, cycles)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def drive_msg(dut, msg: ParsedMsg):
    """Pulse msg_valid for one cycle with the decoded fields.
    Waits one extra RisingEdge so the always_ff NBA assignments from the
    driven cycle have settled before the caller reads the outputs.
    (cocotb resumes in the Active region, before NBA propagation; the extra
    cycle ensures we read in a time step where those NBAs are already done.)
    """
    dut.msg_valid.value = 1
    dut.msg_type.value  = int(msg.msg_type)
    dut.order_ref.value = msg.order_ref
    dut.side.value      = msg.side
    dut.shares.value    = msg.shares
    dut.price.value     = msg.price
    await RisingEdge(dut.clk)   # always_ff fires; NBAs scheduled
    dut.msg_valid.value = 0
    await RisingEdge(dut.clk)   # NBAs from previous edge now committed


def rtl_snap(dut) -> dict:
    return {
        'best_bid_price': int(dut.best_bid_price.value),
        'best_bid_size':  int(dut.best_bid_size.value),
        'best_ask_price': int(dut.best_ask_price.value),
        'best_ask_size':  int(dut.best_ask_size.value),
        'book_valid':     int(dut.book_valid.value),
    }


def check(rtl: dict, golden, label: str = ''):
    tag = f"[{label}] " if label else ""
    assert rtl['best_bid_price'] == golden.best_bid_price, \
        f"{tag}best_bid_price RTL={rtl['best_bid_price']} golden={golden.best_bid_price}"
    assert rtl['best_bid_size']  == golden.best_bid_size, \
        f"{tag}best_bid_size RTL={rtl['best_bid_size']} golden={golden.best_bid_size}"
    assert rtl['best_ask_price'] == golden.best_ask_price, \
        f"{tag}best_ask_price RTL={rtl['best_ask_price']} golden={golden.best_ask_price}"
    assert rtl['best_ask_size']  == golden.best_ask_size, \
        f"{tag}best_ask_size RTL={rtl['best_ask_size']} golden={golden.best_ask_size}"
    assert rtl['book_valid'] == int(golden.valid), \
        f"{tag}book_valid RTL={rtl['book_valid']} golden={int(golden.valid)}"


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_single_bid(dut):
    """Add one bid: best_bid updated, book_valid=0 (no ask yet)."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen  = SynthITCH()
    book = OrderBook()
    msgs = parse_stream(gen.add(ref=1, side='B', shares=500, price=1_500_000))

    await drive_msg(dut, msgs[0])
    golden = book.apply(msgs[0])
    rtl    = rtl_snap(dut)

    check(rtl, golden, 'single_bid')
    assert rtl['book_valid'] == 0, "book_valid must be 0: no ask yet"
    assert rtl['best_bid_price'] == 1_500_000
    assert rtl['best_bid_size']  == 500
    dut._log.info(f"PASS  single bid price={msgs[0].price} size={msgs[0].shares}")


@cocotb.test()
async def test_single_ask(dut):
    """Add one ask: best_ask updated, book_valid=0 (no bid yet)."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen  = SynthITCH()
    book = OrderBook()
    msgs = parse_stream(gen.add(ref=1, side='S', shares=300, price=1_500_100))

    await drive_msg(dut, msgs[0])
    golden = book.apply(msgs[0])
    rtl    = rtl_snap(dut)

    check(rtl, golden, 'single_ask')
    assert rtl['book_valid'] == 0, "book_valid must be 0: no bid yet"
    assert rtl['best_ask_price'] == 1_500_100
    assert rtl['best_ask_size']  == 300
    dut._log.info("PASS  single ask")


@cocotb.test()
async def test_bid_ask_spread(dut):
    """One bid + one ask: book_valid asserts, spread correct."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen  = SynthITCH()
    book = OrderBook()
    msgs = parse_stream(
        gen.add(ref=1, side='B', shares=200, price=1_499_900) +
        gen.add(ref=2, side='S', shares=150, price=1_500_100)
    )

    for m in msgs:
        await drive_msg(dut, m)
        book.apply(m)

    golden = book.snapshot()
    rtl    = rtl_snap(dut)

    check(rtl, golden, 'bid_ask_spread')
    assert rtl['book_valid'] == 1, "book_valid must be 1 after bid+ask"
    dut._log.info(
        f"PASS  bid/ask spread "
        f"bid={rtl['best_bid_price']}/{rtl['best_bid_size']} "
        f"ask={rtl['best_ask_price']}/{rtl['best_ask_size']}"
    )


@cocotb.test()
async def test_better_bid_wins(dut):
    """Two bids at different prices: higher price becomes best bid."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen  = SynthITCH()
    book = OrderBook()
    msgs = parse_stream(
        gen.add(ref=10, side='B', shares=100, price=1_490_000) +
        gen.add(ref=11, side='B', shares=200, price=1_499_900)  # better
    )

    for m in msgs:
        await drive_msg(dut, m)
        book.apply(m)

    golden = book.snapshot()
    rtl    = rtl_snap(dut)

    check(rtl, golden, 'better_bid_wins')
    assert rtl['best_bid_price'] == 1_499_900, \
        f"better bid should win; got {rtl['best_bid_price']}"
    assert rtl['best_bid_size']  == 200
    dut._log.info("PASS  better bid wins")


@cocotb.test()
async def test_better_ask_wins(dut):
    """Two asks at different prices: lower price becomes best ask."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen  = SynthITCH()
    book = OrderBook()
    msgs = parse_stream(
        gen.add(ref=20, side='S', shares=100, price=1_510_000) +
        gen.add(ref=21, side='S', shares=250, price=1_500_500)  # better
    )

    for m in msgs:
        await drive_msg(dut, m)
        book.apply(m)

    golden = book.snapshot()
    rtl    = rtl_snap(dut)

    check(rtl, golden, 'better_ask_wins')
    assert rtl['best_ask_price'] == 1_500_500, \
        f"better ask should win; got {rtl['best_ask_price']}"
    assert rtl['best_ask_size']  == 250
    dut._log.info("PASS  better ask wins")


@cocotb.test()
async def test_same_level_accumulate(dut):
    """Two bids at same price: sizes accumulate on best_bid_size."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen  = SynthITCH()
    book = OrderBook()
    msgs = parse_stream(
        gen.add(ref=30, side='B', shares=100, price=1_500_000) +
        gen.add(ref=31, side='B', shares=300, price=1_500_000)  # same level
    )

    for m in msgs:
        await drive_msg(dut, m)
        book.apply(m)

    golden = book.snapshot()
    rtl    = rtl_snap(dut)

    check(rtl, golden, 'same_level_accumulate')
    assert rtl['best_bid_size'] == 400, \
        f"sizes should accumulate to 400; got {rtl['best_bid_size']}"
    dut._log.info("PASS  same-level accumulation bid_size=400")


@cocotb.test()
async def test_cancel_partial(dut):
    """Cancel partial shares: entry updated; best_bid_size NOT rescanned (M1)."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen  = SynthITCH()
    book = OrderBook()
    msgs = parse_stream(
        gen.add(ref=40,    side='B', shares=500, price=1_500_000) +
        gen.cancel(ref=40, shares=100)
    )

    for m in msgs:
        await drive_msg(dut, m)
        book.apply(m)

    golden = book.snapshot()
    rtl    = rtl_snap(dut)

    check(rtl, golden, 'cancel_partial')
    dut._log.info("PASS  partial cancel (M1: best not rescanned)")


@cocotb.test()
async def test_cancel_full(dut):
    """Cancel full shares: entry invalidated; best NOT rescanned (M1)."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen  = SynthITCH()
    book = OrderBook()
    msgs = parse_stream(
        gen.add(ref=50,    side='S', shares=200, price=1_501_000) +
        gen.cancel(ref=50, shares=200)  # cancel all
    )

    for m in msgs:
        await drive_msg(dut, m)
        book.apply(m)

    golden = book.snapshot()
    rtl    = rtl_snap(dut)

    check(rtl, golden, 'cancel_full')
    dut._log.info("PASS  full cancel (M1: best not rescanned)")


@cocotb.test()
async def test_delete(dut):
    """Delete: entry invalidated; best NOT rescanned (M1)."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen  = SynthITCH()
    book = OrderBook()
    msgs = parse_stream(
        gen.add(ref=60, side='S', shares=200, price=1_501_000) +
        gen.delete(ref=60)
    )

    for m in msgs:
        await drive_msg(dut, m)
        book.apply(m)

    golden = book.snapshot()
    rtl    = rtl_snap(dut)

    check(rtl, golden, 'delete')
    dut._log.info("PASS  delete (M1: best not rescanned)")


@cocotb.test()
async def test_execute_partial(dut):
    """Execute partial: shares reduced in entry; best NOT rescanned (M1)."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen  = SynthITCH()
    book = OrderBook()
    msgs = parse_stream(
        gen.add(ref=70,     side='S', shares=300, price=1_502_000) +
        gen.execute(ref=70, shares=100)
    )

    for m in msgs:
        await drive_msg(dut, m)
        book.apply(m)

    golden = book.snapshot()
    rtl    = rtl_snap(dut)

    check(rtl, golden, 'execute_partial')
    dut._log.info("PASS  partial execute (M1: best not rescanned)")


@cocotb.test()
async def test_golden_stream(dut):
    """Feed scenario_basic() end-to-end; diff RTL against golden at every step."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    gen  = SynthITCH()
    book = OrderBook()
    msgs = parse_stream(gen.scenario_basic())

    for i, msg in enumerate(msgs):
        await drive_msg(dut, msg)
        golden = book.apply(msg)
        rtl    = rtl_snap(dut)
        check(rtl, golden, f'step{i}_{MsgType(msg.msg_type).name}')
        dut._log.info(
            f"step {i} {MsgType(msg.msg_type).name:6s}  "
            f"bid={rtl['best_bid_price']}/{rtl['best_bid_size']}  "
            f"ask={rtl['best_ask_price']}/{rtl['best_ask_size']}  "
            f"valid={rtl['book_valid']}"
        )

    dut._log.info(f"PASS  golden stream ({len(msgs)} messages)")
