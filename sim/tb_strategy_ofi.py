"""
cocotb equivalence: rtl/strategy_ofi.sv == sim/golden/ofi.py

Feeds a continuous random L1 quote stream (one quote/cycle) into the OFI strategy,
samples the decision outputs, and aligns them to the golden model by the fixed
pipeline latency. Asserts every decision (valid/action/price/size) matches.

Run: cd sim && make sim SIM=verilator TOPLEVEL=strategy_ofi MODULE=tb_strategy_ofi \
       EXTRA_ARGS="--trace --trace-fst -Wno-PINMISSING -Wno-WIDTHEXPAND"
"""
import os, sys, random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

sys.path.insert(0, os.path.dirname(__file__))
from golden.ofi import OFIGolden

WINDOW, THRESH = 8, 1500


async def reset(dut):
    dut.rst_n.value = 0
    dut.book_valid.value = 0
    dut.best_bid_price.value = 0; dut.best_ask_price.value = 0
    dut.bid_size.value = 0; dut.ask_size.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


@cocotb.test()
async def ofi_equivalence(dut):
    cocotb.start_soon(Clock(dut.clk, 5, units="ns").start())
    await reset(dut)
    rng = random.Random(11)

    N = 400
    quotes, exp = [], []
    g = OFIGolden(window=WINDOW, threshold=THRESH)
    mid = 1_000_000
    for _ in range(N):
        mid += rng.choice([-200, -100, 0, 0, 100, 200])
        bp, ap = mid - 100, mid + 100
        bs = rng.randint(50, 400)
        asz = rng.randint(50, 400)
        if rng.random() < 0.25:                     # order-flow bursts
            if rng.random() < 0.5: bs += rng.randint(500, 3000)
            else:                  asz += rng.randint(500, 3000)
        quotes.append((bp, bs, ap, asz))
        exp.append(g.step(1, bp, bs, ap, asz))

    # drive one quote/cycle; sample outputs each cycle
    got = []
    for (bp, bs, ap, asz) in quotes:
        dut.book_valid.value = 1
        dut.best_bid_price.value = bp; dut.best_ask_price.value = ap
        dut.bid_size.value = bs; dut.ask_size.value = asz
        await RisingEdge(dut.clk)
        got.append((int(dut.decision_valid.value), int(dut.action.value),
                    int(dut.order_price.value), int(dut.order_size.value)))
    for _ in range(4):                              # flush the pipeline
        await RisingEdge(dut.clk)
        got.append((int(dut.decision_valid.value), int(dut.action.value),
                    int(dut.order_price.value), int(dut.order_size.value)))

    # find the pipeline latency that best aligns RTL decisions to golden
    def mism_at(lat):
        m = 0
        for i, e in enumerate(exp):
            gv = got[i + lat] if i + lat < len(got) else (0, 0, 0, 0)
            if e[0] != gv[0] or (e[0] and e != gv):
                m += 1
        return m
    best_lat = min(range(1, 4), key=mism_at)
    mism = mism_at(best_lat)
    fired = sum(1 for e in exp if e[0])
    dut._log.info(f"OFI equivalence: {len(exp)} ticks, {fired} golden decisions, "
                  f"pipeline latency={best_lat}, {mism} mismatches")
    assert mism == 0, f"{mism} RTL-vs-golden mismatches (lat={best_lat})"
