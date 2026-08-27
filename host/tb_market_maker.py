"""
Unit tests for the market maker's quoting logic (host/market_maker.py).

Checks fair value, quote placement, inventory skew direction, and the position
guard. Pure Python. Run: cd host && python tb_market_maker.py  (exit 0 = pass)
"""

import sys
from market_maker import MarketMaker, MMConfig

_fails = []
def check(name, cond):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _fails.append(name)


def main():
    print("market maker quoting:")
    # skew is an INTEGER tick count -- that is what the RTL implements, and
    # MMConfig.rtl_exact (default) truncates fractional values to 0. This test
    # previously used 0.05, which under RTL-exact maths means NO SKEW AT ALL, so
    # the four skew checks below were silently asserting nothing.
    cfg = MMConfig(half_spread_ticks=10, skew_ticks_per_share=5,
                   quote_size=100, max_position=1000)
    mm = MarketMaker(cfg)

    # balanced book: microprice == mid, quotes symmetric about it
    bp, ap = 1_000_000, 1_000_020        # mid = 1_000_010
    q = mm.quote(bp, 100, ap, 100, inventory=0)
    check("balanced microprice == mid", MarketMaker.microprice(bp, 100, ap, 100) == 1_000_010)
    check("bid = centre - half_spread", q.bid_price == 1_000_000)  # 1_000_010 - 10
    check("ask = centre + half_spread", q.ask_price == 1_000_020)
    check("both sides quoted at flat inventory", q.bid_size == 100 and q.ask_size == 100)

    # bid-heavy book pushes microprice (and the centre) up
    q2 = mm.quote(bp, 900, ap, 100, inventory=0)
    check("bid-heavy -> microprice above mid",
          MarketMaker.microprice(bp, 900, ap, 100) > 1_000_010)
    check("bid-heavy shifts quotes up", q2.bid_price > q.bid_price)

    # LONG inventory -> skew quotes DOWN (keener to sell, shy to buy)
    ql = mm.quote(bp, 100, ap, 100, inventory=200)   # skew = -200*5 = -1000 ticks
    check("long inventory lowers bid", ql.bid_price < q.bid_price)
    check("long inventory lowers ask", ql.ask_price < q.ask_price)

    # SHORT inventory -> skew quotes UP
    qs = mm.quote(bp, 100, ap, 100, inventory=-200)
    check("short inventory raises bid", qs.bid_price > q.bid_price)
    check("short inventory raises ask", qs.ask_price > q.ask_price)

    # position guard: at max long, stop quoting the buy side
    qcap = mm.quote(bp, 100, ap, 100, inventory=1000)
    check("maxed long -> no bid", qcap.bid_size == 0 and qcap.bid_price == 0)
    check("maxed long -> still offers ask", qcap.ask_size == 100)
    qcaps = mm.quote(bp, 100, ap, 100, inventory=-1000)
    check("maxed short -> no ask", qcaps.ask_size == 0)
    check("maxed short -> still bids", qcaps.bid_size == 100)

    # degenerate book: no quote
    qbad = mm.quote(1_000_020, 100, 1_000_000, 100, inventory=0)  # crossed
    check("crossed book -> no quote", qbad.bid_price == 0 and qbad.ask_price == 0)

    print()
    if _fails:
        print(f"FAILED: {len(_fails)} check(s): {', '.join(_fails)}")
        sys.exit(1)
    print("ALL MARKET-MAKER CHECKS PASSED")


if __name__ == '__main__':
    main()
