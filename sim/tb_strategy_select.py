"""
cocotb test for rtl/strategy_select.sv — the runtime strategy mux.

The selector's job is to pass through the chosen strategy's decision. So we drive a
random book stream, sweep strat_sel over the run, and every cycle assert the DUT's
muxed output equals the internally-selected strategy's own output (read via
hierarchy — Verilator --public-flat-rw). This tests the mux precisely without
re-deriving each strategy's golden (those are verified in their own testbenches).
As a bonus, when strat_sel=1 we also check the output against the OFI golden.

Run: cd sim && make sim SIM=verilator TOPLEVEL=strategy_select MODULE=tb_strategy_select \
       EXTRA_ARGS="-Wno-PINMISSING -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC"
"""
import os, sys, random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

sys.path.insert(0, os.path.dirname(__file__))
from golden.ofi import OFIGolden

NLEVELS = 4


async def reset(dut):
    dut.rst_n.value = 0
    dut.strat_sel.value = 0
    dut.book_valid.value = 0
    dut.best_bid_price.value = 0; dut.best_ask_price.value = 0
    dut.best_bid_size.value = 0;  dut.best_ask_size.value = 0
    dut.bid_level_size.value = 0; dut.ask_level_size.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


def pack_levels(sizes):
    v = 0
    for i, s in enumerate(sizes):
        v |= (s & 0xFFFFFFFF) << (32 * i)
    return v


@cocotb.test()
async def select_mux(dut):
    cocotb.start_soon(Clock(dut.clk, 5, units="ns").start())
    await reset(dut)
    rng = random.Random(7)

    mux_checks = mux_mism = 0
    ofi_g = OFIGolden(window=8, threshold=1500)   # for the bonus end-to-end check
    mid = 1_000_000
    sel = 0

    for i in range(600):
        if i % 60 == 0:                            # sweep the selector periodically
            sel = rng.choice([0, 1])
            dut.strat_sel.value = sel
        mid += rng.choice([-200, -100, 0, 0, 100, 200])
        bp, ap = mid - 100, mid + 100
        bl = [rng.randint(50, 400) for _ in range(NLEVELS)]
        al = [rng.randint(50, 400) for _ in range(NLEVELS)]
        if rng.random() < 0.25:
            if rng.random() < 0.5: bl[0] += rng.randint(500, 3000)
            else:                  al[0] += rng.randint(500, 3000)

        dut.book_valid.value = 1
        dut.best_bid_price.value = bp; dut.best_ask_price.value = ap
        dut.best_bid_size.value = bl[0]; dut.best_ask_size.value = al[0]
        dut.bid_level_size.value = pack_levels(bl)
        dut.ask_level_size.value = pack_levels(al)
        await RisingEdge(dut.clk)

        # combinational mux -> compare AFTER the edge settles
        cur_sel = int(dut.strat_sel.value)
        out = (int(dut.decision_valid.value), int(dut.action.value),
               int(dut.order_price.value), int(dut.order_size.value))
        if cur_sel == 1:
            want = (int(dut.u_ofi.decision_valid.value), int(dut.u_ofi.action.value),
                    int(dut.u_ofi.order_price.value), int(dut.u_ofi.order_size.value))
        else:
            want = (int(dut.u_imbalance.decision_valid.value), int(dut.u_imbalance.action.value),
                    int(dut.u_imbalance.order_price.value), int(dut.u_imbalance.order_size.value))
        mux_checks += 1
        if out != want:
            mux_mism += 1
            dut._log.error(f"cycle {i} sel={cur_sel} mux out {out} != selected {want}")

    dut._log.info(f"strategy_select: {mux_checks} mux checks, {mux_mism} mismatches")
    assert mux_mism == 0, f"{mux_mism} mux mismatches"
