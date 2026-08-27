#!/usr/bin/env python3
"""
Equivalence tests for the remaining host strategies against their RTL golden models.

  host/ofi_signal.py    ==  sim/golden/ofi.py      (mirror of strategy_ofi.sv)
  host/market_maker.py  ==  sim/golden/mm_rtl.py   (mirror of strategy_market_maker.sv)

tb_strategy_sw_equiv.py already covers host/strategy_sw.py vs
golden/strategy_imbalance.py. This closes the gap for the other two, so every
software strategy that has RTL behind it is checked decision-for-decision --
otherwise "the software mirrors the FPGA" is an unbacked claim, and trading on
it means trading on something that was never compared to the hardware.

Both models are driven with MATCHING parameters. That matters, because the
defaults do NOT agree:

    market maker  golden HALF_SPREAD=200 SKEW=2      host half_spread_ticks=3
                                                          skew_ticks_per_share=0.02
    OFI           golden window=8                     host window=10

Defaults disagreeing is not itself a bug -- the host ones were tuned for the live
equity path -- but it does mean "same strategy" only holds when you configure them
to be. The default gap is reported separately at the end.

Exit code 0 = equivalent, 1 = a mismatch was found (CI-friendly).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "host"))

from golden.itch_parser   import parse_stream
from golden.order_book_m2 import OrderBookM2
from golden.ofi           import OFIGolden
from golden.mm_rtl        import mm_quote
from golden.ofi_maker     import OFIMakerGolden
from ofi_signal           import OFISignal
from market_maker         import MarketMaker, MMConfig
from ofi_maker            import OFIMaker, OFIMakerConfig
import replay_gen


# --------------------------------------------------------------------------
def _host_ofi(dec):
    """host OFISignal.update -> Decision | None"""
    return None if dec is None else (dec.action, dec.price, dec.size)


def _gold_ofi(t):
    """golden OFIGolden.step -> (decision_valid, action, price, size) tuple"""
    dv, action, price, size = t
    return (action, price, size) if dv else None


def run_ofi(n, seed, window=8, threshold=1500):
    """Both OFI models over one synthetic stream, matched parameters."""
    msgs = parse_stream(replay_gen.build_synthetic(n, seed))
    book = OrderBookM2()
    gold = OFIGolden(window=window, threshold=threshold)
    host = OFISignal(window=window, threshold=threshold)

    mismatches, fires = [], 0
    for i, m in enumerate(msgs):
        book.apply(m)
        s = book.snapshot()
        g = gold.step(s.book_valid, s.best_bid_price, s.best_bid_size,
                      s.best_ask_price, s.best_ask_size)
        h = host.update(s.best_bid_price, s.best_bid_size,
                        s.best_ask_price, s.best_ask_size,
                        book_valid=s.book_valid)
        gt, ht = _gold_ofi(g), _host_ofi(h)
        if gt is not None:
            fires += 1
        if gt != ht:
            mismatches.append((i, gt, ht))
    return len(msgs), fires, mismatches


def run_mm(n, seed, half_spread=200, skew=2, quote_size=100, max_position=1000):
    """Both market-maker models over one synthetic stream, matched parameters.

    Inventory is walked deterministically so the skew/position-guard paths are
    actually exercised rather than sitting at flat the whole run.
    """
    msgs = parse_stream(replay_gen.build_synthetic(n, seed))
    book = OrderBookM2()
    host = MarketMaker(MMConfig(half_spread_ticks=half_spread,
                                skew_ticks_per_share=skew,
                                quote_size=quote_size,
                                max_position=max_position,
                                min_book_size=1))

    mismatches, quotes = [], 0
    inv = 0
    for i, m in enumerate(msgs):
        book.apply(m)
        s = book.snapshot()
        inv = ((i * 137) % (2 * max_position + 1)) - max_position   # sweep the range

        gv, gbp, gbq, gap, gaq = mm_quote(
            s.book_valid, s.best_bid_price, s.best_ask_price,
            s.best_bid_size, s.best_ask_size, inv,
            HALF_SPREAD=half_spread, SKEW=skew,
            QUOTE_SIZE=quote_size, MAX_POSITION=max_position)

        q = host.quote(s.best_bid_price, s.best_bid_size,
                       s.best_ask_price, s.best_ask_size, inv)

        gt = (gbp, gbq, gap, gaq) if gv else (0, 0, 0, 0)
        ht = (q.bid_price, q.bid_size, q.ask_price, q.ask_size)
        if gv:
            quotes += 1
        if gt != ht:
            mismatches.append((i, gt, ht))
    return len(msgs), quotes, mismatches


def run_ofi_maker(n, seed, half_spread=200, skew=2, quote_size=100,
                  max_position=1000, window=8, max_tilt=150):
    """Both OFI-maker models over one stream. The tilt is an arithmetic shift in
    the RTL (ofi >> alpha_shift); the host expresses the same thing as
    alpha_num/alpha_den, so shift=2 must equal num=1/den=4."""
    msgs = parse_stream(replay_gen.build_synthetic(n, seed))
    book = OrderBookM2()
    gold = OFIMakerGolden(half_spread=half_spread, skew=skew, quote_size=quote_size,
                          max_position=max_position, ofi_window=window,
                          alpha_shift=2, max_tilt=max_tilt)
    host = OFIMaker(OFIMakerConfig(half_spread=half_spread, skew=skew,
                                   quote_size=quote_size, max_position=max_position,
                                   ofi_window=window, alpha_num=1, alpha_den=4,
                                   max_tilt=max_tilt))

    mismatches, quotes = [], 0
    for i, m in enumerate(msgs):
        book.apply(m)
        s = book.snapshot()
        inv = ((i * 137) % (2 * max_position + 1)) - max_position

        gv, gbp, gbq, gap, gaq = gold.quote(
            s.book_valid, s.best_bid_price, s.best_bid_size,
            s.best_ask_price, s.best_ask_size, inv)
        q = host.quote(s.best_bid_price, s.best_bid_size,
                       s.best_ask_price, s.best_ask_size, inv,
                       book_valid=s.book_valid)

        gt = (gbp, gbq, gap, gaq) if gv else (0, 0, 0, 0)
        ht = (q.bid_price, q.bid_size, q.ask_price, q.ask_size)
        if gv:
            quotes += 1
        if gt != ht:
            mismatches.append((i, gt, ht))
    return len(msgs), quotes, mismatches


# --------------------------------------------------------------------------
def report(title, runner, cases, **kw):
    print(f"\n=== {title} ===")
    total_m = total_f = 0
    bad = 0
    for n, seed in cases:
        msgs, fires, mm = runner(n, seed, **kw)
        total_m += msgs
        total_f += fires
        status = "OK " if not mm else "FAIL"
        print(f"  [{status}] n={n:<6} seed={seed}: {msgs:6} msgs, "
              f"{fires:5} golden fires, {len(mm)} mismatch")
        if mm:
            bad += len(mm)
            for idx, gt, ht in mm[:3]:
                print(f"          msg {idx}: golden={gt}  host={ht}")
    print(f"\n  totals: {total_m} msgs, {total_f} golden fires, {bad} mismatches")
    return bad


def main():
    cases = [(4000, s) for s in range(1, 6)]
    fails = 0

    fails += report("ofi_signal.py  vs  golden/ofi.py  (strategy_ofi.sv)",
                    run_ofi, cases, window=8, threshold=1500)

    fails += report("market_maker.py  vs  golden/mm_rtl.py  (strategy_market_maker.sv)",
                    run_mm, cases, half_spread=200, skew=2)

    fails += report("ofi_maker.py  vs  golden/ofi_maker.py  (strategy_ofi_maker.sv)",
                    run_ofi_maker, cases)

    print("\n=== default-parameter gap (not a correctness failure) ===")
    print(f"  market maker : golden HALF_SPREAD=200 SKEW=2   "
          f"host {MMConfig().half_spread_ticks} / {MMConfig().skew_ticks_per_share}")
    print(f"  OFI          : golden window=8                 host {OFISignal().window}")
    print("  -> the host defaults were tuned for the live equity path; they must be")
    print("     set explicitly to match the RTL if you want identical behaviour.")

    if fails:
        print(f"\nRESULT: {fails} MISMATCHES — the host models do NOT reproduce the RTL")
        return 1
    print("\nRESULT: EQUIVALENT — both host models reproduce their golden model exactly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
