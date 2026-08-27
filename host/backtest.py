"""
Backtest harness — measure strategies on the same synthetic quote stream.

Compares two fundamentally different styles on identical data so the trade-offs
are visible in hard numbers:

  * TAKER  — the imbalance strategy (strategy_sw.py). It CROSSES the spread
    (buys at the ask, sells at the bid), so it pays the spread on every trade.
  * MAKER  — the market maker (market_maker.py). It POSTS resting quotes on both
    sides and EARNS the spread when the market trades through them, managing
    inventory with quote skew.

Fill model (coarse L1, no look-ahead): a strategy quotes using the book at tick
t; fills are decided by the mid at tick t+1.
  * Taker: an aggressive order fills immediately at its limit (ask for a buy, bid
    for a sell) — that is the cost of immediacy.
  * Maker: a resting BUY at price B fills iff next_mid <= B (the market came down
    to us); a resting SELL at A fills iff next_mid >= A. At most one side per tick.

Both are charged the same fee (fee_bps of notional per fill) and marked to the
current mid each tick, so PnL, drawdown and a Sharpe-like ratio are comparable.

Run:
    cd host && python backtest.py                 # default sweep of seeds
    cd host && python backtest.py --n 4000 --seed 7
"""

import argparse
import os
import re
import random
import statistics as stats

from quote_sim import gen_quotes
from strategy_sw import SoftwareStrategy
from market_maker import MarketMaker, MMConfig
from portfolio import Portfolio, TICKS_PER_USD

USD = TICKS_PER_USD


def to_ticks(usd):
    return int(round(usd * USD))


def _stream_ticks(n, seed):
    """gen_quotes -> list of (bid_p, bid_s, ask_p, ask_s) in integer ticks."""
    return [(to_ticks(b), bs, to_ticks(a), asz)
            for (b, bs, a, asz) in gen_quotes(n, seed)]


# real live-Alpaca quotes logged as e.g. "bid=80@296.9400  ask=40@296.9900"
_QUOTE_RE = re.compile(r"bid=(\d+)@([\d.]+)\s+ask=(\d+)@([\d.]+)")


def real_stream_ticks(log_path):
    """Parse a live-run log into a real (bid_p, bid_s, ask_p, ask_s) tick stream.

    Source is the SIGNAL lines of a live Alpaca soak (reports/soak_aapl.log), so
    these are REAL market quotes. CAVEATS, stated honestly: they are one symbol
    (AAPL), sampled only at moments the strategy fired (imbalanced books, not a
    uniform time series), and consecutive lines can repeat an unchanged quote.
    Still real prices/sizes, which is the point of this run.
    """
    stream = []
    with open(log_path, errors="ignore") as fh:
        for line in fh:
            m = _QUOTE_RE.search(line)
            if m:
                bs, bp, asz, ap = m.groups()
                stream.append((to_ticks(float(bp)), int(bs),
                               to_ticks(float(ap)), int(asz)))
    return stream


def _metrics(name, pf, curve, fills, fees):
    """Assemble a result row from the marked-equity curve (already net of fees)."""
    net = curve[-1] if curve else 0.0
    peak, dd = float("-inf"), 0.0
    for e in curve:
        peak = max(peak, e)
        dd = max(dd, peak - e)
    deltas = [curve[i] - curve[i - 1] for i in range(1, len(curve))]
    if len(deltas) > 1 and stats.pstdev(deltas) > 0:
        sharpe = stats.fmean(deltas) / stats.pstdev(deltas)
    else:
        sharpe = 0.0
    inv = sum(abs(b.pos) for b in pf.books.values())
    return {
        "name": name, "fills": fills, "fees": fees, "net_pnl": net,
        "pnl_per_fill": (net / fills) if fills else 0.0,
        "max_dd": dd, "sharpe": sharpe, "final_inv": inv,
    }


def run_taker(stream, fee_bps=1.0, threshold=15):
    pf = Portfolio()
    strat = SoftwareStrategy(bid_thresh=threshold, ask_thresh=threshold)
    curve, fills, fees = [], 0, 0.0
    sym = "SIM"
    for i in range(len(stream) - 1):
        bp, bs, ap, asz = stream[i]
        dec = strat.evaluate(True, bp, bs, ap, asz)
        mid = (bp + ap) // 2
        if dec is not None:
            # aggressive: fills immediately at its own limit price
            price = dec.price
            pf.on_fill(sym, dec.action, dec.size, price)
            fee = (fee_bps / 1e4) * (price / USD) * dec.size
            fees += fee
            fills += 1
        # mark to the NEXT mid, record equity net of cumulative fees
        nbp, _, nap, _ = stream[i + 1]
        pf.mark_price(sym, (nbp + nap) // 2)
        curve.append(pf.equity() - fees)
    return _metrics("taker (crosses spread)", pf, curve, fills, fees)


def run_ofi(stream, fee_bps=1.0, window=10, threshold=1500):
    """OFI directional taker (Cont-Kukanov-Stoikov): trades the order-flow signal,
    crossing the spread like the imbalance taker. Same fill model as run_taker so
    the two signals are compared apples-to-apples."""
    from ofi_signal import OFISignal
    pf = Portfolio()
    sig = OFISignal(window=window, threshold=threshold)
    curve, fills, fees = [], 0, 0.0
    sym = "SIM"
    for i in range(len(stream) - 1):
        bp, bs, ap, asz = stream[i]
        dec = sig.update(bp, bs, ap, asz)
        if dec is not None:
            pf.on_fill(sym, dec.action, dec.size, dec.price)
            fees += (fee_bps / 1e4) * (dec.price / USD) * dec.size
            fills += 1
        nbp, _, nap, _ = stream[i + 1]
        pf.mark_price(sym, (nbp + nap) // 2)
        curve.append(pf.equity() - fees)
    return _metrics("OFI taker (order-flow)", pf, curve, fills, fees)


def run_maker(stream, cfg: MMConfig = None, fee_bps=1.0,
              fill_prob=0.25, seed=0):
    """Fill model with BOTH revenue and risk, the way a real maker experiences it:

      * SPREAD CAPTURE (revenue): each side is hit by noise flow with probability
        `fill_prob` per tick, filling at our quoted price — we pocket the distance
        from the mid. Both sides can fill in one tick (earn the full spread).
      * ADVERSE SELECTION / INVENTORY (risk): after fills, the position is marked
        to the moving mid; on one-sided drift the inventory bleeds until quote
        skew pulls it back to flat.
    """
    pf = Portfolio()
    mm = MarketMaker(cfg)
    rng = random.Random(seed)
    curve, fills, fees = [], 0, 0.0
    sym = "SIM"
    for i in range(len(stream) - 1):
        bp, bs, ap, asz = stream[i]
        # mark to the current mid BEFORE fills so on_fill's PnL / kill-switch use
        # a real price (not an unset mark on the first fill of a symbol).
        pf.mark_price(sym, (bp + ap) // 2)
        inv = pf.books[sym].pos if sym in pf.books else 0
        q = mm.quote(bp, bs, ap, asz, inv)
        nbp, _, nap, _ = stream[i + 1]
        next_mid = (nbp + nap) // 2

        # noise flow hits each resting side independently
        if q.bid_size and q.bid_price and rng.random() < fill_prob:
            pf.on_fill(sym, 0, q.bid_size, q.bid_price)          # we BUY at our bid
            fees += (fee_bps / 1e4) * (q.bid_price / USD) * q.bid_size
            fills += 1
        if q.ask_size and q.ask_price and rng.random() < fill_prob:
            pf.on_fill(sym, 1, q.ask_size, q.ask_price)          # we SELL at our ask
            fees += (fee_bps / 1e4) * (q.ask_price / USD) * q.ask_size
            fills += 1

        pf.mark_price(sym, next_mid)
        curve.append(pf.equity() - fees)
    return _metrics("maker (earns spread)", pf, curve, fills, fees)


def run_ofi_maker(stream, cfg=None, fee_bps=1.0, fill_prob=0.25, seed=0):
    """OFI-driven maker — same flat-fill model as run_maker so the ONLY difference
    vs the plain maker is the OFI drift tilt on the quote centre."""
    from ofi_maker import OFIMaker
    pf = Portfolio()
    mm = OFIMaker(cfg)
    rng = random.Random(seed)
    curve, fills, fees = [], 0, 0.0
    sym = "SIM"
    for i in range(len(stream) - 1):
        bp, bs, ap, asz = stream[i]
        pf.mark_price(sym, (bp + ap) // 2)
        inv = pf.books[sym].pos if sym in pf.books else 0
        q = mm.quote(bp, bs, ap, asz, inv)
        nbp, _, nap, _ = stream[i + 1]
        if q.bid_size and q.bid_price and rng.random() < fill_prob:
            pf.on_fill(sym, 0, q.bid_size, q.bid_price)
            fees += (fee_bps / 1e4) * (q.bid_price / USD) * q.bid_size
            fills += 1
        if q.ask_size and q.ask_price and rng.random() < fill_prob:
            pf.on_fill(sym, 1, q.ask_size, q.ask_price)
            fees += (fee_bps / 1e4) * (q.ask_price / USD) * q.ask_size
            fills += 1
        pf.mark_price(sym, (nbp + nap) // 2)
        curve.append(pf.equity() - fees)
    return _metrics("OFI-driven maker", pf, curve, fills, fees)


def run_as(stream, cfg=None, fee_bps=1.0, fill_prob=0.25, seed=0):
    """Avellaneda-Stoikov maker — SAME flat-fill model as run_maker so it is an
    apples-to-apples comparison: only the QUOTING LOGIC differs (fixed spread +
    linear skew vs A-S reservation price + optimal spread from vol/inventory)."""
    from avellaneda_stoikov import AvellanedaStoikov
    pf = Portfolio()
    mm = AvellanedaStoikov(cfg)
    rng = random.Random(seed)
    curve, fills, fees = [], 0, 0.0
    sym = "SIM"
    for i in range(len(stream) - 1):
        bp, bs, ap, asz = stream[i]
        pf.mark_price(sym, (bp + ap) // 2)
        inv = pf.books[sym].pos if sym in pf.books else 0
        q = mm.quote(bp, bs, ap, asz, inv)
        nbp, _, nap, _ = stream[i + 1]
        next_mid = (nbp + nap) // 2
        if q.bid_size and q.bid_price and rng.random() < fill_prob:
            pf.on_fill(sym, 0, q.bid_size, q.bid_price)
            fees += (fee_bps / 1e4) * (q.bid_price / USD) * q.bid_size
            fills += 1
        if q.ask_size and q.ask_price and rng.random() < fill_prob:
            pf.on_fill(sym, 1, q.ask_size, q.ask_price)
            fees += (fee_bps / 1e4) * (q.ask_price / USD) * q.ask_size
            fills += 1
        pf.mark_price(sym, next_mid)
        curve.append(pf.equity() - fees)
    return _metrics("A-S maker (optimal)", pf, curve, fills, fees)


def _print_table(rows):
    hdr = f"{'strategy':<26}{'fills':>7}{'net PnL':>12}{'fees':>10}" \
          f"{'PnL/fill':>10}{'max DD':>10}{'Sharpe*':>9}{'|inv|':>7}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['name']:<26}{r['fills']:>7}{r['net_pnl']:>12.2f}"
              f"{r['fees']:>10.2f}{r['pnl_per_fill']:>10.3f}{r['max_dd']:>10.2f}"
              f"{r['sharpe']:>9.3f}{r['final_inv']:>7}")


def main():
    ap = argparse.ArgumentParser(description="backtest taker vs market maker")
    ap.add_argument("--n", type=int, default=4000, help="quotes per stream")
    ap.add_argument("--seed", type=int, default=None, help="single seed (else sweep)")
    ap.add_argument("--fee-bps", type=float, default=1.0)
    ap.add_argument("--half-spread", type=int, default=200,
                    help="maker half-spread in ticks (200 = $0.02)")
    ap.add_argument("--real-log", default=None,
                    help="backtest on REAL quotes parsed from a live-run log "
                         "(e.g. ../reports/soak_aapl.log) instead of synthetic")
    args = ap.parse_args()

    mmcfg = MMConfig(half_spread_ticks=args.half_spread)

    if args.real_log:
        stream = real_stream_ticks(args.real_log)
        if len(stream) < 3:
            raise SystemExit(f"no quotes parsed from {args.real_log}")
        print(f"=== Backtest on REAL data: {os.path.basename(args.real_log)}  "
              f"({len(stream)} quotes, fee={args.fee_bps}bps, "
              f"maker half-spread={args.half_spread} ticks) ===")
        print("  caveat: real AAPL quotes sampled at signal moments (see "
              "real_stream_ticks docstring)\n")
        rows = [run_taker(stream, fee_bps=args.fee_bps),
                run_maker(stream, cfg=mmcfg, fee_bps=args.fee_bps, seed=1)]
        _print_table(rows)
        return

    seeds = [args.seed] if args.seed is not None else [1, 2, 3, 7, 11]
    print(f"=== Backtest: taker vs maker  (n={args.n}, fee={args.fee_bps}bps, "
          f"maker half-spread={args.half_spread} ticks) ===\n")

    agg = {"taker (crosses spread)": [], "OFI taker (order-flow)": [],
           "maker (earns spread)": [], "A-S maker (optimal)": [], "OFI-driven maker": []}
    for seed in seeds:
        stream = _stream_ticks(args.n, seed)
        rows = [run_taker(stream, fee_bps=args.fee_bps),
                run_ofi(stream, fee_bps=args.fee_bps),
                run_maker(stream, cfg=mmcfg, fee_bps=args.fee_bps, seed=seed),
                run_as(stream, fee_bps=args.fee_bps, seed=seed),
                run_ofi_maker(stream, fee_bps=args.fee_bps, seed=seed)]
        print(f"-- seed {seed} --")
        _print_table(rows)
        print()
        for r in rows:
            agg[r["name"]].append(r["net_pnl"])

    print("=== mean net PnL across seeds ===")
    for name, pnls in agg.items():
        print(f"  {name:<26} ${stats.fmean(pnls):>10.2f}  "
              f"(per-seed: {', '.join(f'{p:.0f}' for p in pnls)})")


if __name__ == '__main__':
    main()
