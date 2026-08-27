"""
Unit tests for the HFT decision overlay: portfolio.py + hft_logic.py.

Checks the money math (realized/unrealized PnL, exposure), the kill switch, and
each pre-trade gate's veto behaviour. Pure Python, no market connection.

Run:  cd host && python tb_hft_logic.py     (exit 0 = all pass)
"""

import sys
from dataclasses import dataclass
from portfolio import Portfolio, TICKS_PER_USD
from hft_logic import (PreTradeGauntlet, HFTConfig,
                       FAIR_VALUE, COST, VOLATILITY, LIQUIDITY, STALENESS,
                       INVENTORY, EXPOSURE, KILLED, PASSED)

USD = TICKS_PER_USD


@dataclass
class Dec:
    action: int
    price: int
    size: int


_fails = []
def check(name, cond):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _fails.append(name)


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


# --------------------------------------------------------------------------
def test_portfolio_pnl():
    print("portfolio PnL / exposure:")
    pf = Portfolio()
    # BUY 100 @ $150, then SELL 100 @ $151 -> realized +$100
    pf.on_fill("AAPL", 0, 100, 150 * USD)
    check("long position after buy", pf.books["AAPL"].pos == 100)
    check("avg cost = 150", approx(pf.books["AAPL"].avg_cost, 150.0))
    pf.on_fill("AAPL", 1, 100, 151 * USD)
    check("flat after sell", pf.books["AAPL"].pos == 0)
    check("realized +$100 (100 * $1)", approx(pf.realized(), 100.0))

    # unrealized: BUY 200 @ $100, mark to $105 -> +$1000 open
    pf2 = Portfolio()
    pf2.on_fill("X", 0, 200, 100 * USD)
    pf2.mark_price("X", 105 * USD)
    check("unrealized +$1000", approx(pf2.unrealized(), 1000.0))
    check("net exposure = 200*$105", approx(pf2.net_exposure(), 21000.0))
    check("gross == net when one long", approx(pf2.gross_exposure(), 21000.0))

    # short PnL: SELL 100 @ $50, buy back @ $48 -> +$200
    pf3 = Portfolio()
    pf3.on_fill("S", 1, 100, 50 * USD)
    check("short position", pf3.books["S"].pos == -100)
    pf3.on_fill("S", 0, 100, 48 * USD)
    check("short realized +$200", approx(pf3.realized(), 200.0))

    # gross vs net with mixed book: +100 @100 and -100 @100 (two symbols)
    pf4 = Portfolio()
    pf4.on_fill("L", 0, 100, 100 * USD); pf4.mark_price("L", 100 * USD)
    pf4.on_fill("Sh", 1, 100, 100 * USD); pf4.mark_price("Sh", 100 * USD)
    check("net ~ 0 (hedged)", approx(pf4.net_exposure(), 0.0))
    check("gross = $20000 (both legs)", approx(pf4.gross_exposure(), 20000.0))


def test_kill_switch():
    print("kill switch (daily loss):")
    pf = Portfolio(daily_loss_limit_usd=500.0)
    # lose $600: buy 100 @ $100, sell @ $94 -> -$600
    pf.on_fill("K", 0, 100, 100 * USD)
    pf.on_fill("K", 1, 100, 94 * USD)
    check("realized -$600", approx(pf.realized(), -600.0))
    check("kill switch tripped", pf.halted is True)
    check("halt reason set", pf.halt_reason is not None)


def _quote(bid, ask, bsz, asz):
    return bid * USD, bsz, ask * USD, asz


def test_gates():
    print("pre-trade gates:")
    cfg = HFTConfig(min_edge_ticks=1, fee_bps=0.0, max_vol_bps=1e9,
                    min_book_size=100, max_quote_age_s=1e9,
                    inventory_soft_frac=0.6, position_cap=1000)

    # FAIR VALUE veto: BUY but microprice below mid (ask-heavy book).
    g = PreTradeGauntlet(Portfolio(), cfg)
    bp, bs, ap, asz = _quote(100.00, 100.02, 100, 900)  # heavy ask -> microprice < mid
    g.observe("T", (bp + ap)//2)
    _, r = g.evaluate("T", Dec(0, ap, 100), bp, bs, ap, asz, position=0)
    check("BUY vetoed by fair_value (ask-heavy)", r == FAIR_VALUE)

    # FAIR VALUE pass: bid-heavy -> microprice above mid -> BUY confirmed.
    g = PreTradeGauntlet(Portfolio(), cfg)
    bp, bs, ap, asz = _quote(100.00, 100.02, 900, 100)
    g.observe("T", (bp + ap)//2)
    dec, r = g.evaluate("T", Dec(0, ap, 100), bp, bs, ap, asz, position=0)
    check("BUY passes on bid-heavy book", r == PASSED and dec is not None)

    # LIQUIDITY veto: thin book.
    g = PreTradeGauntlet(Portfolio(), cfg)
    bp, bs, ap, asz = _quote(100.00, 100.02, 900, 10)   # ask side too thin
    g.observe("T", (bp + ap)//2)
    _, r = g.evaluate("T", Dec(0, ap, 100), bp, bs, ap, asz, position=0)
    check("thin book vetoed by liquidity", r == LIQUIDITY)

    # COST veto: fee bigger than the microprice edge.
    cfg_cost = HFTConfig(min_edge_ticks=1, fee_bps=50.0, max_vol_bps=1e9,
                         min_book_size=100, max_quote_age_s=1e9)
    g = PreTradeGauntlet(Portfolio(), cfg_cost)
    bp, bs, ap, asz = _quote(100.00, 100.02, 600, 100)
    g.observe("T", (bp + ap)//2)
    _, r = g.evaluate("T", Dec(0, ap, 100), bp, bs, ap, asz, position=0)
    check("edge below fee vetoed by cost", r == COST)

    # VOLATILITY veto: feed a jumpy mid history.
    cfg_vol = HFTConfig(min_edge_ticks=1, fee_bps=0.0, max_vol_bps=5.0,
                        min_book_size=100, max_quote_age_s=1e9, vol_window=10)
    g = PreTradeGauntlet(Portfolio(), cfg_vol)
    for i in range(10):
        g.observe("T", int((100 + (i % 2) * 5) * USD))   # oscillate $100/$105
    bp, bs, ap, asz = _quote(100.00, 100.02, 600, 100)
    _, r = g.evaluate("T", Dec(0, ap, 100), bp, bs, ap, asz, position=0)
    check("high vol vetoed", r == VOLATILITY)

    # INVENTORY veto: already long past soft cap, ADD blocked.
    g = PreTradeGauntlet(Portfolio(), cfg)
    bp, bs, ap, asz = _quote(100.00, 100.02, 900, 100)
    g.observe("T", (bp + ap)//2)
    _, r = g.evaluate("T", Dec(0, ap, 100), bp, bs, ap, asz, position=700)  # >0.6*1000
    check("adding when heavy vetoed by inventory", r == INVENTORY)
    # reducing (SELL when long, ask-heavy for sell confirm) still allowed
    g = PreTradeGauntlet(Portfolio(), cfg)
    bp, bs, ap, asz = _quote(100.00, 100.02, 100, 900)  # ask-heavy -> SELL confirmed
    g.observe("T", (bp + ap)//2)
    dec, r = g.evaluate("T", Dec(1, bp, 100), bp, bs, ap, asz, position=700)
    check("reducing when heavy still passes", r == PASSED)

    # EXPOSURE clamp: tiny gross cap -> size clamped down.
    pf = Portfolio(max_gross_notional_usd=10_000.0)   # $10k cap
    g = PreTradeGauntlet(pf, cfg)
    bp, bs, ap, asz = _quote(100.00, 100.02, 900, 100)
    g.observe("T", (bp + ap)//2)
    dec, r = g.evaluate("T", Dec(0, ap, 1000), bp, bs, ap, asz, position=0)
    # $10k / ~$100 = ~99 shares max
    check("size clamped to gross cap", r == PASSED and dec.size < 1000 and dec.size <= 100)

    # KILL SWITCH veto: halted portfolio blocks everything.
    pf = Portfolio(); pf.halted = True
    g = PreTradeGauntlet(pf, cfg)
    bp, bs, ap, asz = _quote(100.00, 100.02, 900, 100)
    g.observe("T", (bp + ap)//2)
    _, r = g.evaluate("T", Dec(0, ap, 100), bp, bs, ap, asz, position=0)
    check("halted portfolio vetoes trade", r == KILLED)


if __name__ == '__main__':
    test_portfolio_pnl()
    test_kill_switch()
    test_gates()
    print()
    if _fails:
        print(f"FAILED: {len(_fails)} check(s): {', '.join(_fails)}")
        sys.exit(1)
    print("ALL HFT-LOGIC CHECKS PASSED")
