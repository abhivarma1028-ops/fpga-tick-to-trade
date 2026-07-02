"""
FPGA-vs-software shadow logger.

Feeds the SAME synthetic live-quote stream (host/quote_sim.py — the one the live
prototype trades on) to both:
  * the software strategy (host/strategy_sw.py — the FPGA-equivalent signal), and
  * the RTL, by rebuilding each L1 quote as ITCH (delete+add bid+add ask) so the
    order_book_m2 top-of-book == the quote, then sampling the strategy decision.

Writes a per-quote CSV (reports/shadow_report.csv) with both signals + a match
flag, and prints the mismatch summary — the FPGA-vs-software equivalence report
to have in hand before the AWS run.

Run:
    cd sim && SHADOW_N=80 SHADOW_SEED=1 make SIM=questa \
        TOPLEVEL=tick_to_trade_top MODULE=tb_shadow_quotes
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, ReadOnly
import os, sys, csv
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'host'))

from synth_itch import SynthITCH
from quote_sim import gen_quotes
from strategy_sw import SoftwareStrategy

CLK = 5
SETTLE = 360           # cycles for delete-RESCAN + adds + strategy to settle
BID_REF, ASK_REF = 1, 2


def to_ticks(usd):
    return int(round(usd * 10_000))


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


async def drive_msg(dut, framed):
    raw = framed[2:]   # strip 2-byte length prefix
    for i, b in enumerate(raw):
        dut.s_axis_tvalid.value = 1
        dut.s_axis_tdata.value = b
        dut.s_axis_tlast.value = 1 if i == len(raw) - 1 else 0
        await ReadOnly()
        while not int(dut.s_axis_tready.value):
            await RisingEdge(dut.clk); await ReadOnly()
        await RisingEdge(dut.clk)
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tlast.value = 0


@cocotb.test()
async def test_shadow_quotes(dut):
    n = int(os.environ.get('SHADOW_N', '80'))
    seed = int(os.environ.get('SHADOW_SEED', '1'))
    quotes = gen_quotes(n, seed)

    cocotb.start_soon(Clock(dut.clk, CLK, unit='ns').start())
    await reset(dut)

    # RTL strategy-signal monitor (the decision the FPGA would emit)
    cur = {'pkt': None, 'seen': False}

    async def strat_mon():
        while True:
            await RisingEdge(dut.clk)
            if int(dut.u_strategy.decision_valid.value):
                cur['pkt'] = (int(dut.u_strategy.action.value),
                              int(dut.u_strategy.order_price.value),
                              int(dut.u_strategy.order_size.value))
                cur['seen'] = True
    cocotb.start_soon(strat_mon())

    sw = SoftwareStrategy()
    gen = SynthITCH()
    rows = []
    sw_sig = rtl_sig = match = 0

    for i, (bid, bs, ask, asz) in enumerate(quotes):
        bt, at = to_ticks(bid), to_ticks(ask)

        # ---- software signal (FPGA-equivalent), book always valid like live ----
        d = sw.evaluate(True, bt, bs, at, asz)
        sw_dec = (d.action, d.price, d.size) if d else None

        # ---- RTL: rebuild this quote as L1 book, sample the strategy signal ----
        await drive_msg(dut, gen.delete(ref=BID_REF))
        await drive_msg(dut, gen.delete(ref=ASK_REF))
        await drive_msg(dut, gen.add(ref=BID_REF, side='B', shares=bs, price=bt))
        await drive_msg(dut, gen.add(ref=ASK_REF, side='S', shares=asz, price=at))
        cur['seen'] = False                 # only count fires from this settled quote
        await ClockCycles(dut.clk, SETTLE)
        rtl_dec = cur['pkt'] if cur['seen'] else None

        if sw_dec: sw_sig += 1
        if rtl_dec: rtl_sig += 1
        ok = (sw_dec == rtl_dec)
        if ok: match += 1

        rows.append({
            'tick': i, 'bid': bid, 'bid_size': bs, 'ask': ask, 'ask_size': asz,
            'sw_action':  '' if not sw_dec  else ('BUY' if sw_dec[0] == 0 else 'SELL'),
            'sw_price':   '' if not sw_dec  else sw_dec[1],
            'sw_size':    '' if not sw_dec  else sw_dec[2],
            'rtl_action': '' if not rtl_dec else ('BUY' if rtl_dec[0] == 0 else 'SELL'),
            'rtl_price':  '' if not rtl_dec else rtl_dec[1],
            'rtl_size':   '' if not rtl_dec else rtl_dec[2],
            'match': int(ok),
        })

    # ---- write CSV ----
    out_dir = os.path.join(os.path.dirname(__file__), '..', 'reports')
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.abspath(os.path.join(out_dir, 'shadow_report.csv'))
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    mism = [r for r in rows if not r['match']]
    rate = 100.0 * match / len(rows)
    dut._log.info("=" * 70)
    dut._log.info(f"SHADOW (FPGA vs software): n={n} seed={seed}")
    dut._log.info(f"  software signals : {sw_sig}")
    dut._log.info(f"  RTL signals      : {rtl_sig}")
    dut._log.info(f"  per-quote match  : {match}/{len(rows)}  ({rate:.1f}%)")
    dut._log.info(f"  CSV              : {path}")
    for r in mism[:10]:
        dut._log.info(f"   MISMATCH tick {r['tick']}: sw={r['sw_action']}/{r['sw_price']}/{r['sw_size']} "
                      f"rtl={r['rtl_action']}/{r['rtl_price']}/{r['rtl_size']}")
    dut._log.info("=" * 70)

    assert sw_sig > 0 and rtl_sig > 0, "no signals produced — check the feed"
    assert rate >= 95.0, f"FPGA-vs-software match rate {rate:.1f}% too low — see {path}"
    dut._log.info(f"PASS  FPGA == software on {rate:.1f}% of quotes")
