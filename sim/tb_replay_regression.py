"""
Phase-2 replay regression — RTL vs golden equivalence on realistic flow.

Drives a long, randomized-realistic ITCH stream (replay_gen) through
tick_to_trade_top and checks the RTL against the golden models:

  CHECK A (asserted) — ORDER BOOK equivalence.
      After each message settles, sample u_book's best bid/ask price+size and
      book_valid; diff against golden OrderBookM2. This validates the M2
      BRAM+RESCAN book on the edge cases hand-crafted tests miss (multi-level
      aggregation, partial reduce, deletes that empty a level, replace).

  CHECK B (reported) — STRATEGY decisions.
      A background monitor records every u_strategy decision pulse. Repeats on a
      held book (the COOLDOWN re-fire) are collapsed; the resulting per-update
      decisions are logged alongside the golden strategy for comparison. Risk
      gating + position cycle-semantics are deferred to the Phase-3 hardware run.

Run:
    cd sim && make SIM=questa TOPLEVEL=tick_to_trade_top MODULE=tb_replay_regression
    REPLAY_N=300 REPLAY_SEED=1 make SIM=questa TOPLEVEL=tick_to_trade_top MODULE=tb_replay_regression
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, ReadOnly
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

import replay_gen
from golden.itch_parser import parse_message
from golden.order_book_m2 import OrderBookM2
from golden.strategy_imbalance import StrategyImbalance

CLK = 5  # ns, 200 MHz
SETTLE = 400  # cycles to let parser + RESCAN finish per message (RESCAN ~256)


def split_framed(stream: bytes):
    """Yield (raw_payload, parsed_msg) for each framed message."""
    off = 0
    out = []
    while off + 2 <= len(stream):
        length = int.from_bytes(stream[off:off + 2], 'big')
        off += 2
        raw = stream[off:off + length]
        off += length
        out.append((raw, parse_message(raw)))
    return out


async def reset(dut):
    dut.rst_n.value = 0
    dut.halt.value = 0
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tdata.value = 0
    dut.s_axis_tlast.value = 0
    dut.m_axis_tready.value = 1
    for s in ('s_axil_awaddr', 's_axil_awvalid', 's_axil_wdata', 's_axil_wstrb',
              's_axil_wvalid', 's_axil_bready', 's_axil_araddr', 's_axil_arvalid',
              's_axil_rready'):
        getattr(dut, s).value = 0
    await ClockCycles(dut.clk, 8)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def drive_stream(dut, raw: bytes):
    """Feed one framing-stripped ITCH message, honouring s_axis_tready."""
    for i, byte in enumerate(raw):
        dut.s_axis_tvalid.value = 1
        dut.s_axis_tdata.value = byte
        dut.s_axis_tlast.value = 1 if i == len(raw) - 1 else 0
        await ReadOnly()
        while not int(dut.s_axis_tready.value):
            await RisingEdge(dut.clk)
            await ReadOnly()
        await RisingEdge(dut.clk)
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tlast.value = 0


def sample_book(dut):
    return (int(dut.u_book.best_bid_price.value),
            int(dut.u_book.best_bid_size.value),
            int(dut.u_book.best_ask_price.value),
            int(dut.u_book.best_ask_size.value),
            int(dut.u_book.book_valid.value))


@cocotb.test()
async def test_replay_book_equivalence(dut):
    n = int(os.environ.get('REPLAY_N', '150'))
    seed = int(os.environ.get('REPLAY_SEED', '1'))
    stream = replay_gen.build_synthetic(n, seed)
    msgs = split_framed(stream)

    cocotb.start_soon(Clock(dut.clk, CLK, unit='ns').start())
    await reset(dut)

    # background strategy monitor (Check B — reported only)
    rtl_strat = []

    async def strat_mon():
        last = None
        while True:
            await RisingEdge(dut.clk)
            if int(dut.u_strategy.decision_valid.value):
                pkt = (int(dut.u_strategy.action.value),
                       int(dut.u_strategy.order_price.value),
                       int(dut.u_strategy.order_size.value))
                if pkt != last:           # collapse held-book re-fires
                    rtl_strat.append(pkt)
                last = pkt
    cocotb.start_soon(strat_mon())

    book = OrderBookM2()
    strat = StrategyImbalance()
    gold_strat = []
    mismatches = []

    for i, (raw, pm) in enumerate(msgs):
        if pm is None:
            continue
        # golden
        book.apply(pm)
        snap = book.snapshot()
        gdec = strat.evaluate(snap)
        if gdec is not None:
            pkt = (gdec.action, gdec.price, gdec.size)
            if not gold_strat or gold_strat[-1] != pkt:   # collapse like the RTL monitor
                gold_strat.append(pkt)

        # RTL: drive + settle + sample
        await drive_stream(dut, raw)
        await ClockCycles(dut.clk, SETTLE)
        await ReadOnly()
        rtl = sample_book(dut)
        gold = (snap.best_bid_price, snap.best_bid_size,
                snap.best_ask_price, snap.best_ask_size, int(snap.book_valid))
        if rtl != gold and len(mismatches) < 20:
            mismatches.append((i, pm.msg_type, gold, rtl))
        elif rtl != gold:
            mismatches.append((i, pm.msg_type, gold, rtl))
        await RisingEdge(dut.clk)

    dut._log.info("=" * 64)
    dut._log.info(f"Phase-2 replay: n={n} seed={seed}  messages={len(msgs)}")
    dut._log.info(f"  CHECK A book mismatches : {len(mismatches)}")
    dut._log.info(f"  CHECK B golden strat decisions={len(gold_strat)} "
                  f"rtl strat (collapsed)={len(rtl_strat)}")
    for mi, mt, g, r in mismatches[:20]:
        dut._log.info(f"   msg#{mi} type=0x{mt:02x}  golden={g}  rtl={r}")
    dut._log.info("=" * 64)

    assert not mismatches, \
        f"{len(mismatches)} book mismatch(es) vs golden — see log (first: {mismatches[0] if mismatches else None})"
