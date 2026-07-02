"""
cocotb equivalence test: rtl/strategy_market_maker.sv == sim/golden/mm_rtl.py

Drives random top-of-book states + inventory into the hardware market maker and
asserts every quote (bid/ask price+qty, valid) matches the golden model exactly.

Run (Verilator, fast/free):
    cd sim && make SIM=verilator TOPLEVEL=strategy_market_maker MODULE=tb_strategy_market_maker
Or Questa:
    cd sim && make SIM=questa    TOPLEVEL=strategy_market_maker MODULE=tb_strategy_market_maker
"""

import os
import sys
import random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

sys.path.insert(0, os.path.dirname(__file__))
from golden.mm_rtl import mm_quote

# must match the module's default parameters
P = dict(HALF_SPREAD=200, SKEW=2, QUOTE_SIZE=100, MAX_POSITION=1000)


async def reset(dut):
    dut.rst_n.value = 0
    dut.book_valid.value = 0
    dut.best_bid_price.value = 0
    dut.best_ask_price.value = 0
    dut.bid_size.value = 0
    dut.ask_size.value = 0
    dut.inventory.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


@cocotb.test()
async def mm_equivalence(dut):
    cocotb.start_soon(Clock(dut.clk, 5, units="ns").start())   # 200 MHz
    await reset(dut)

    rng = random.Random(7)
    checks = 0
    mismatches = 0

    for _ in range(500):
        # random normal-ish book around a ~$193 mid, plus some degenerate cases
        mid = rng.randint(500_000, 5_000_000)
        half = rng.randint(1, 5000)
        bp = mid - half
        ap = mid + half
        bs = rng.randint(0, 3000)
        asz = rng.randint(0, 3000)
        inv = rng.randint(-1200, 1200)
        bv = rng.random() > 0.1
        # occasional crossed/degenerate book
        if rng.random() < 0.1:
            ap, bp = bp, ap

        dut.book_valid.value = int(bv)
        dut.best_bid_price.value = bp
        dut.best_ask_price.value = ap
        dut.bid_size.value = bs
        dut.ask_size.value = asz
        dut.inventory.value = inv

        # pipelined divider: hold inputs stable and wait out the full latency
        # (1 input reg + 64 divider stages + 1 output reg) so the registered
        # outputs settle to THIS input's quote.
        for _ in range(70):
            await RisingEdge(dut.clk)

        exp = mm_quote(bv, bp, ap, bs, asz, inv, **P)
        got_valid = int(dut.quote_valid.value)
        # compare valid; when valid, compare the quote fields
        if exp[0] != got_valid:
            mismatches += 1
            dut._log.error(f"valid mismatch: exp {exp[0]} got {got_valid} "
                           f"(bp={bp} ap={ap} bs={bs} as={asz} inv={inv})")
        elif got_valid:
            got = (1, int(dut.bid_price.value), int(dut.bid_qty.value),
                   int(dut.ask_price.value), int(dut.ask_qty.value))
            if got != exp:
                mismatches += 1
                dut._log.error(f"quote mismatch exp {exp} got {got} "
                               f"(bp={bp} ap={ap} bs={bs} as={asz} inv={inv})")
        checks += 1

    dut._log.info(f"market-maker equivalence: {checks} checks, {mismatches} mismatches")
    assert mismatches == 0, f"{mismatches} RTL-vs-golden mismatches"
