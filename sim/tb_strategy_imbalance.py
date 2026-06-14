"""
cocotb testbench for strategy_imbalance.sv
Drives book state directly and checks decision_valid / action / price outputs.

Run:
    cd sim && make SIM=questa TOPLEVEL=strategy_imbalance MODULE=tb_strategy_imbalance
"""

import cocotb
from cocotb.clock    import Clock
from cocotb.triggers import RisingEdge, ClockCycles
import sys, os
sys.path.insert(0, os.path.dirname(__file__))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BID_THRESH       = 15     # mirrors RTL parameter default
ASK_THRESH       = 15
MAX_SPREAD_TICKS = 1000   # mirrors RTL parameter default ($0.10)
COOLDOWN_CYCLES  = 8      # mirrors RTL parameter default
BASE_LOT         = 100    # mirrors RTL parameter default
MAX_LOT          = 250    # mirrors RTL parameter default
LOT_SIZE         = BASE_LOT  # tier-1 (default) lot size


NLEVELS = 4   # mirrors RTL parameter default


def _pack_levels(sizes) -> int:
    """Pack a list of per-level sizes (level 0 first) into the flattened bus."""
    bus = 0
    for i, v in enumerate(sizes):
        bus |= (int(v) & 0xFFFF_FFFF) << (i * 32)
    return bus


async def reset(dut, cycles: int = 5):
    dut.rst_n.value           = 0
    dut.book_valid.value      = 0
    dut.best_bid_price.value  = 0
    dut.best_ask_price.value  = 0
    dut.bid_level_size.value  = 0
    dut.ask_level_size.value  = 0
    await ClockCycles(dut.clk, cycles)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


def set_book(dut, bid_price, bid_size, ask_price, ask_size, valid=1):
    """Drive a single-level (top-of-book only) book. The depth-weighted strategy
    reduces to the plain ratio when only level 0 is populated."""
    dut.book_valid.value     = valid
    dut.best_bid_price.value = bid_price
    dut.best_ask_price.value = ask_price
    dut.bid_level_size.value = _pack_levels([bid_size])
    dut.ask_level_size.value = _pack_levels([ask_size])


def set_book_levels(dut, bid_price, bid_sizes, ask_price, ask_sizes, valid=1):
    """Drive a multi-level book: bid_sizes/ask_sizes are per-level (level 0 first)."""
    dut.book_valid.value     = valid
    dut.best_bid_price.value = bid_price
    dut.best_ask_price.value = ask_price
    dut.bid_level_size.value = _pack_levels(bid_sizes)
    dut.ask_level_size.value = _pack_levels(ask_sizes)


def dec_snap(dut) -> dict:
    return {
        'valid':       int(dut.decision_valid.value),
        'action':      int(dut.action.value),
        'order_price': int(dut.order_price.value),
        'order_size':  int(dut.order_size.value),
    }


async def await_decision(dut, max_cycles: int = 20) -> dict | None:
    """Wait until decision_valid pulses; returns None on timeout.
    Reads one cycle after the NBA edge (same pattern as order-book testbench).
    """
    for _ in range(max_cycles):
        await RisingEdge(dut.clk)
        if int(dut.decision_valid.value):
            return dec_snap(dut)
    return None


async def assert_no_decision(dut, cycles: int = 10):
    """Verify decision_valid stays 0 for `cycles` clock cycles."""
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        assert int(dut.decision_valid.value) == 0, \
            f"Unexpected decision: action={dut.action.value}"


async def cycles_until_decision(dut, max_cycles: int = 40) -> int | None:
    """Count rising edges until the next decision_valid pulse; None on timeout."""
    n = 0
    for _ in range(max_cycles):
        await RisingEdge(dut.clk)
        n += 1
        if int(dut.decision_valid.value):
            return n
    return None


# ---------------------------------------------------------------------------
# Golden helper — matches RTL cross-multiply logic exactly
# ---------------------------------------------------------------------------

def golden_decision(bid_price, bid_size, ask_price, ask_size,
                    bid_thresh=BID_THRESH, ask_thresh=ASK_THRESH):
    """Returns ('BUY', price) or ('SELL', price) or None."""
    if bid_size * 10 > bid_thresh * ask_size and ask_price > 0:
        return ('BUY',  ask_price)
    if ask_size * 10 > ask_thresh * bid_size and bid_price > 0:
        return ('SELL', bid_price)
    return None


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_no_decision_balanced(dut):
    """Balanced book: no signal generated."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    set_book(dut, bid_price=1_499_900, bid_size=100,
                  ask_price=1_500_100, ask_size=100)
    await assert_no_decision(dut, cycles=10)
    dut._log.info("PASS  balanced book → no decision")


@cocotb.test()
async def test_buy_signal(dut):
    """bid_size=200, ask_size=100 (2:1 > 1.5 threshold) → BUY at ask_price."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    BID_P, BID_S = 1_499_900, 200
    ASK_P, ASK_S = 1_500_100, 100
    set_book(dut, bid_price=BID_P, bid_size=BID_S,
                  ask_price=ASK_P, ask_size=ASK_S)

    dec = await await_decision(dut)

    assert dec is not None,         "Expected BUY decision, got timeout"
    assert dec['valid']       == 1
    assert dec['action']      == 0, f"Expected BUY (0), got {dec['action']}"
    assert dec['order_price'] == ASK_P, \
        f"BUY should lift the ask; got price={dec['order_price']}"
    assert dec['order_size']  == LOT_SIZE

    g = golden_decision(BID_P, BID_S, ASK_P, ASK_S)
    assert g == ('BUY', ASK_P)

    dut._log.info(f"PASS  buy signal action={dec['action']} price={dec['order_price']}")


@cocotb.test()
async def test_sell_signal(dut):
    """ask_size=200, bid_size=100 (2:1 > 1.5 threshold) → SELL at bid_price."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    BID_P, BID_S = 1_499_900, 100
    ASK_P, ASK_S = 1_500_100, 200
    set_book(dut, bid_price=BID_P, bid_size=BID_S,
                  ask_price=ASK_P, ask_size=ASK_S)

    dec = await await_decision(dut)

    assert dec is not None,         "Expected SELL decision, got timeout"
    assert dec['action']      == 1, f"Expected SELL (1), got {dec['action']}"
    assert dec['order_price'] == BID_P, \
        f"SELL should hit the bid; got price={dec['order_price']}"
    assert dec['order_size']  == LOT_SIZE

    g = golden_decision(BID_P, BID_S, ASK_P, ASK_S)
    assert g == ('SELL', BID_P)

    dut._log.info(f"PASS  sell signal action={dec['action']} price={dec['order_price']}")


@cocotb.test()
async def test_threshold_exact_no_trigger(dut):
    """bid_size * 10 == BID_THRESH * ask_size: NOT strictly greater → no BUY."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    # 150 * 10 = 1500, 15 * 100 = 1500 → equal, not greater → no decision
    set_book(dut, bid_price=1_499_900, bid_size=150,
                  ask_price=1_500_100, ask_size=100)
    await assert_no_decision(dut, cycles=10)
    dut._log.info("PASS  exact threshold (equal) → no decision")


@cocotb.test()
async def test_threshold_one_above_triggers(dut):
    """bid_size=151, ask_size=100: 1510 > 1500 → BUY fires."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    set_book(dut, bid_price=1_499_900, bid_size=151,
                  ask_price=1_500_100, ask_size=100)

    dec = await await_decision(dut)
    assert dec is not None,    "Expected BUY at bid_size=151"
    assert dec['action'] == 0, "Expected BUY"
    dut._log.info("PASS  one-above threshold triggers BUY")


@cocotb.test()
async def test_book_invalid_no_decision(dut):
    """book_valid=0: strategy must not fire even with extreme imbalance."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    set_book(dut, bid_price=1_499_900, bid_size=10_000,
                  ask_price=1_500_100, ask_size=1,
                  valid=0)
    await assert_no_decision(dut, cycles=10)
    dut._log.info("PASS  book_valid=0 → no decision")


@cocotb.test()
async def test_prev_valid_gate(dut):
    """book_valid pulse for ONE cycle only: prev_valid was 0 → no decision."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    # Drive book_valid for exactly 1 cycle then drop it
    set_book(dut, bid_price=1_499_900, bid_size=200,
                  ask_price=1_500_100, ask_size=100, valid=1)
    await RisingEdge(dut.clk)
    dut.book_valid.value = 0

    await assert_no_decision(dut, cycles=10)
    dut._log.info("PASS  single-cycle book_valid: prev_valid gate blocks decision")


@cocotb.test()
async def test_buy_order_price_is_ask(dut):
    """BUY order_price == best_ask_price (aggressive: lift the ask)."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    ASK_P = 1_500_250
    set_book(dut, bid_price=1_499_750, bid_size=300,
                  ask_price=ASK_P,     ask_size=100)

    dec = await await_decision(dut)
    assert dec is not None
    assert dec['action']      == 0
    assert dec['order_price'] == ASK_P, \
        f"BUY price should equal ask={ASK_P}; got {dec['order_price']}"
    assert dec['order_size']  == LOT_SIZE
    dut._log.info(f"PASS  BUY order_price == ask_price={ASK_P}")


@cocotb.test()
async def test_sell_order_price_is_bid(dut):
    """SELL order_price == best_bid_price (aggressive: hit the bid)."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    BID_P = 1_499_750
    set_book(dut, bid_price=BID_P,     bid_size=100,
                  ask_price=1_500_250, ask_size=300)

    dec = await await_decision(dut)
    assert dec is not None
    assert dec['action']      == 1
    assert dec['order_price'] == BID_P, \
        f"SELL price should equal bid={BID_P}; got {dec['order_price']}"
    assert dec['order_size']  == LOT_SIZE
    dut._log.info(f"PASS  SELL order_price == bid_price={BID_P}")


# ---------------------------------------------------------------------------
# M2 guard tests — spread guard + cooldown
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_spread_guard_blocks_wide_market(dut):
    """Strong buy imbalance but spread (2000 ticks) > MAX_SPREAD_TICKS → no trade."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    # spread = 1_501_000 - 1_499_000 = 2000 ticks ($0.20) > 1000 limit
    set_book(dut, bid_price=1_499_000, bid_size=500,
                  ask_price=1_501_000, ask_size=100)
    await assert_no_decision(dut, cycles=15)
    dut._log.info("PASS  wide spread blocks trade despite 5:1 imbalance")


@cocotb.test()
async def test_spread_at_limit_fires(dut):
    """Spread exactly == MAX_SPREAD_TICKS is allowed (<=), so BUY still fires."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    ASK_P = 1_501_000   # spread = 1_501_000 - 1_500_000 = 1000 == limit
    set_book(dut, bid_price=1_500_000, bid_size=200,
                  ask_price=ASK_P,     ask_size=100)

    dec = await await_decision(dut)
    assert dec is not None,         "Spread at the limit should be allowed"
    assert dec['action']      == 0, f"Expected BUY, got {dec['action']}"
    assert dec['order_price'] == ASK_P
    dut._log.info("PASS  spread at limit (==MAX) fires BUY")


@cocotb.test()
async def test_crossed_book_blocked(dut):
    """Locked/crossed book (ask <= bid) is not a normal quote → no trade."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    # ask == bid (locked); strong imbalance must still be suppressed
    set_book(dut, bid_price=1_500_000, bid_size=500,
                  ask_price=1_500_000, ask_size=100)
    await assert_no_decision(dut, cycles=15)
    dut._log.info("PASS  locked book (ask==bid) blocks trade")


@cocotb.test()
async def test_cooldown_enforced(dut):
    """A persistent imbalance must NOT fire every cycle: gap >= COOLDOWN_CYCLES."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    # Hold a buy-imbalanced, in-spread book continuously
    set_book(dut, bid_price=1_499_900, bid_size=200,
                  ask_price=1_500_100, ask_size=100)

    dec1 = await await_decision(dut)
    assert dec1 is not None, "Expected first BUY decision"

    gap = await cycles_until_decision(dut)
    assert gap is not None, "Second decision never fired after cooldown"
    assert gap >= COOLDOWN_CYCLES, \
        f"Cooldown not enforced: only {gap} cycles between decisions " \
        f"(expected >= {COOLDOWN_CYCLES})"
    dut._log.info(f"PASS  cooldown enforced: {gap} cycles between decisions")


# ---------------------------------------------------------------------------
# M2 imbalance-scaled lot sizing
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_lot_tier1_base(dut):
    """Imbalance just past threshold (2:1) → base lot size."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)
    # bid 200 vs ask 100: 2000 > 1500 (trigger) but not > 3000 (2x) → tier 1
    set_book(dut, bid_price=1_499_900, bid_size=200,
                  ask_price=1_500_100, ask_size=100)
    dec = await await_decision(dut)
    assert dec is not None and dec['action'] == 0
    assert dec['order_size'] == BASE_LOT, \
        f"tier-1 size should be {BASE_LOT}, got {dec['order_size']}"
    dut._log.info(f"PASS  tier-1 lot = {dec['order_size']}")


@cocotb.test()
async def test_lot_tier2_scales(dut):
    """Imbalance > 2× threshold → 2× base lot."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)
    # bid 400 vs ask 100: 4000 > 3000 (2x) but not > 4500 (3x) → tier 2
    set_book(dut, bid_price=1_499_900, bid_size=400,
                  ask_price=1_500_100, ask_size=100)
    dec = await await_decision(dut)
    assert dec is not None and dec['action'] == 0
    assert dec['order_size'] == 2 * BASE_LOT, \
        f"tier-2 size should be {2*BASE_LOT}, got {dec['order_size']}"
    dut._log.info(f"PASS  tier-2 lot = {dec['order_size']}")


@cocotb.test()
async def test_lot_tier3_capped(dut):
    """Very strong imbalance (>3× threshold) → 3× base, but capped at MAX_LOT."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)
    # bid 1000 vs ask 100: 10000 > 4500 (3x) → 3*BASE=300, capped to MAX_LOT
    set_book(dut, bid_price=1_499_900, bid_size=1000,
                  ask_price=1_500_100, ask_size=100)
    dec = await await_decision(dut)
    assert dec is not None and dec['action'] == 0
    assert dec['order_size'] == MAX_LOT, \
        f"tier-3 size should cap at MAX_LOT={MAX_LOT}, got {dec['order_size']}"
    dut._log.info(f"PASS  tier-3 lot capped at {dec['order_size']}")


@cocotb.test()
async def test_lot_sell_tier2_scales(dut):
    """SELL side also scales: ask 400 vs bid 100 → 2× base lot."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)
    set_book(dut, bid_price=1_499_900, bid_size=100,
                  ask_price=1_500_100, ask_size=400)
    dec = await await_decision(dut)
    assert dec is not None and dec['action'] == 1
    assert dec['order_size'] == 2 * BASE_LOT, \
        f"SELL tier-2 size should be {2*BASE_LOT}, got {dec['order_size']}"
    dut._log.info(f"PASS  SELL tier-2 lot = {dec['order_size']}")


# ---------------------------------------------------------------------------
# Depth-weighted imbalance — uses book depth beyond the touch
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_depth_supported_bid_triggers_buy(dut):
    """Touch is balanced (100 vs 100) so a top-of-book-only strategy would NOT
    trade — but the bid is supported at 3 deeper levels while the ask is thin,
    so the depth-weighted volume tips it into a BUY."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    # w_bid = 4*100+3*100+2*100+1*100 = 1000 ; w_ask = 4*100 = 400 → BUY
    set_book_levels(dut, bid_price=1_499_900, bid_sizes=[100, 100, 100, 100],
                         ask_price=1_500_100, ask_sizes=[100, 0, 0, 0])
    dec = await await_decision(dut)
    assert dec is not None,        "depth-supported bid should trigger BUY"
    assert dec['action'] == 0,     f"expected BUY, got {dec['action']}"
    assert dec['order_price'] == 1_500_100
    dut._log.info("PASS  depth-supported bid → BUY (touch alone is balanced)")


@cocotb.test()
async def test_depth_supported_ask_triggers_sell(dut):
    """Symmetric: thin bid, ask supported across depth → SELL."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    set_book_levels(dut, bid_price=1_499_900, bid_sizes=[100, 0, 0, 0],
                         ask_price=1_500_100, ask_sizes=[100, 100, 100, 100])
    dec = await await_decision(dut)
    assert dec is not None,    "depth-supported ask should trigger SELL"
    assert dec['action'] == 1, f"expected SELL, got {dec['action']}"
    assert dec['order_price'] == 1_499_900
    dut._log.info("PASS  depth-supported ask → SELL")


@cocotb.test()
async def test_depth_balanced_no_trade(dut):
    """Equal depth on both sides → balanced weighted volume → no decision."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    set_book_levels(dut, bid_price=1_499_900, bid_sizes=[100, 100, 100, 100],
                         ask_price=1_500_100, ask_sizes=[100, 100, 100, 100])
    await assert_no_decision(dut, cycles=12)
    dut._log.info("PASS  balanced depth → no decision")


@cocotb.test()
async def test_depth_strong_imbalance_scales_lot(dut):
    """Heavy bid depth vs thin ask → very strong weighted imbalance → lot caps."""
    cocotb.start_soon(Clock(dut.clk, 5, unit='ns').start())
    await reset(dut)

    # w_bid = (4+3+2+1)*400 = 4000 ; w_ask = 400 → 40000 > 3*15*400=18000 → tier3 cap
    set_book_levels(dut, bid_price=1_499_900, bid_sizes=[400, 400, 400, 400],
                         ask_price=1_500_100, ask_sizes=[100, 0, 0, 0])
    dec = await await_decision(dut)
    assert dec is not None and dec['action'] == 0
    assert dec['order_size'] == MAX_LOT, \
        f"strong depth imbalance should cap at {MAX_LOT}, got {dec['order_size']}"
    dut._log.info(f"PASS  strong depth imbalance → lot capped at {dec['order_size']}")
