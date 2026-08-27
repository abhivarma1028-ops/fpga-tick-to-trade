"""
cocotb equivalence: rtl/strategy_ofi_maker.sv == sim/golden/ofi_maker.py

The OFI-driven maker has cross-tick OFI window state AND the 64-stage microprice
divider, so we feed one quote/cycle continuously and align the registered quote
outputs to the golden by the fixed pipeline latency (1 + DIV_W + 1 = 66).

Run: cd sim && make sim SIM=verilator TOPLEVEL=strategy_ofi_maker \
       MODULE=tb_strategy_ofi_maker \
       EXTRA_ARGS="-Wno-PINMISSING -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC"
"""
import os, sys, random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

sys.path.insert(0, os.path.dirname(__file__))
from golden.ofi_maker import OFIMakerGolden

P = dict(half_spread=200, skew=2, quote_size=100, max_position=1000,
         ofi_window=8, alpha_shift=2, max_tilt=150)


async def reset(dut):
    dut.rst_n.value = 0
    dut.book_valid.value = 0
    dut.best_bid_price.value = 0; dut.best_ask_price.value = 0
    dut.bid_size.value = 0; dut.ask_size.value = 0
    dut.inventory.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


@cocotb.test()
async def ofi_maker_equivalence(dut):
    cocotb.start_soon(Clock(dut.clk, 5, units="ns").start())
    await reset(dut)
    rng = random.Random(5)

    N = 500
    quotes, invs, exp = [], [], []
    g = OFIMakerGolden(**P)
    mid = 1_000_000
    inv = 0
    for _ in range(N):
        mid += rng.choice([-200, -100, 0, 0, 100, 200])
        bp, ap = mid - 100, mid + 100
        bs = rng.randint(50, 400); asz = rng.randint(50, 400)
        if rng.random() < 0.25:
            if rng.random() < 0.5: bs += rng.randint(500, 3000)
            else:                  asz += rng.randint(500, 3000)
        inv += rng.choice([-100, 0, 0, 100])
        inv = max(-1200, min(1200, inv))
        quotes.append((bp, bs, ap, asz)); invs.append(inv)
        exp.append(g.quote(1, bp, bs, ap, asz, inv))

    got = []
    for (bp, bs, ap, asz), iv in zip(quotes, invs):
        dut.book_valid.value = 1
        dut.best_bid_price.value = bp; dut.best_ask_price.value = ap
        dut.bid_size.value = bs; dut.ask_size.value = asz
        dut.inventory.value = iv
        await RisingEdge(dut.clk)
        got.append((int(dut.quote_valid.value), int(dut.bid_price.value),
                    int(dut.bid_qty.value), int(dut.ask_price.value), int(dut.ask_qty.value)))
    for _ in range(70):
        await RisingEdge(dut.clk)
        got.append((int(dut.quote_valid.value), int(dut.bid_price.value),
                    int(dut.bid_qty.value), int(dut.ask_price.value), int(dut.ask_qty.value)))

    def mism_at(lat):
        m = 0
        for i, e in enumerate(exp):
            gv = got[i + lat] if i + lat < len(got) else (0, 0, 0, 0, 0)
            if e[0] != gv[0] or (e[0] and e != gv):
                m += 1
        return m
    best = min(range(60, 72), key=mism_at)
    mism = mism_at(best)
    dut._log.info(f"OFI-maker equivalence: {len(exp)} quotes, pipeline latency={best}, "
                  f"{mism} mismatches")
    assert mism == 0, f"{mism} mismatches (lat={best})"
