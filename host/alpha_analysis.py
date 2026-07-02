"""
Signal alpha analysis — does the trading signal actually PREDICT anything?

A strategy can be correct-looking and still have no edge. This tool measures the
predictive power of the book-imbalance / microprice signal directly, the way a
quant researcher would, BEFORE trusting it to trade:

  For every tick with a signal s_i, look H ticks into the future and measure the
  mid-price move r = mid[i+H] - mid[i]. Then across all such ticks:

    * HIT RATE      P(sign(r) == sign(s))            50% = coin flip = no edge
    * EDGE (ticks)  mean( sign(s) * r )              >0 = signal points the right way
    * IC            corr(signal_strength, r)         information coefficient; 0 = none

Two signals are tested:
  * OBI  — order-book imbalance (bid_size - ask_size)/(bid_size + ask_size)
  * MPX  — microprice deviation (microprice - mid), size-weighted fair value

Run on three data sets so the result is interpretable:
  1. RANDOM WALK (quote_sim)      — no built-in relationship -> expect ~no edge.
                                     This is the honest null: proves the tool isn't
                                     manufacturing alpha.
  2. INJECTED ALPHA (control)     — mid deliberately drifts toward the imbalance ->
                                     expect a STRONG hit rate. Proves the tool DETECTS
                                     edge when it genuinely exists.
  3. REAL AAPL (soak log, if present) — the actual question, on real quotes.

Run:  cd host && python alpha_analysis.py
      cd host && python alpha_analysis.py --real-log ../reports/soak_aapl.log
"""

import argparse
import os
import random
import statistics as stats

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from quote_sim import gen_quotes
from backtest import to_ticks, real_stream_ticks

HORIZONS = [1, 2, 5, 10, 20]


# --------------------------------------------------------------------------
# signals
def obi(bp, bs, ap, asz):
    denom = bs + asz
    return (bs - asz) / denom if denom else 0.0


def mpx(bp, bs, ap, asz):
    denom = bs + asz
    if not denom:
        return 0.0
    microprice = (bp * asz + ap * bs) / denom
    return microprice - (bp + ap) / 2.0        # deviation from mid, in ticks


# --------------------------------------------------------------------------
def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = stats.fmean(xs), stats.fmean(ys)
    sx = stats.pstdev(xs); sy = stats.pstdev(ys)
    if sx == 0 or sy == 0:
        return 0.0
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    return cov / (sx * sy)


def analyze(stream, signal_fn, horizons=HORIZONS, min_abs=0.0):
    """stream = list of (bid_p, bid_s, ask_p, ask_s) in ticks.
    Returns {H: {hit, edge_ticks, ic, n}} for each horizon."""
    mids = [(bp + ap) / 2.0 for (bp, _, ap, _) in stream]
    sig = [signal_fn(*q) for q in stream]
    out = {}
    for H in horizons:
        hits = signed = 0
        n_moved = 0                      # ticks where the price actually moved
        strengths, rets = [], []
        n = 0
        for i in range(len(stream) - H):
            s = sig[i]
            if abs(s) <= min_abs:
                continue
            r = mids[i + H] - mids[i]
            n += 1
            if r != 0:                   # flat outcomes are excluded from hit rate
                n_moved += 1
                if (r > 0) == (s > 0):
                    hits += 1
            signed += (1 if s > 0 else -1) * r
            strengths.append(s); rets.append(r)
        out[H] = {
            "n": n,
            "hit": (hits / n_moved) if n_moved else 0.0,
            "edge_ticks": (signed / n) if n else 0.0,
            "ic": _pearson(strengths, rets),
        }
    return out


# --------------------------------------------------------------------------
# data sets
def stream_random(n, seed):
    return [(to_ticks(b), bs, to_ticks(a), asz)
            for (b, bs, a, asz) in gen_quotes(n, seed)]


def stream_injected(n, seed, strength=0.04):
    """Positive control: the CURRENT imbalance drives the NEXT mid move, so the
    signal genuinely PREDICTS the forward return and should show a high hit rate.
    Sanity-checks that the tool detects edge when it truly exists."""
    rng = random.Random(seed)
    mid = 150.00
    out = []
    for _ in range(n):
        bs = rng.randint(80, 300)
        asz = rng.randint(80, 300)
        if rng.random() < 0.30:
            if rng.random() < 0.5:
                bs = rng.randint(800, 2500)
            else:
                asz = rng.randint(800, 2500)
        # record the quote at the CURRENT mid, then let THIS imbalance move the
        # next mid (so imbalance[i] predicts mid[i+1]-mid[i]).
        out.append((to_ticks(mid - 0.025), bs, to_ticks(mid + 0.025), asz))
        imb = (bs - asz) / (bs + asz)
        mid += strength * imb + rng.choice([-0.01, 0.0, 0.01])
        mid = max(50.0, mid)
    return out


# --------------------------------------------------------------------------
def _print_block(title, results_by_signal):
    print(f"\n=== {title} ===")
    for sig_name, res in results_by_signal.items():
        print(f"  signal {sig_name}:")
        print(f"    {'horizon':>8}{'n':>8}{'hit%':>8}{'edge(ticks)':>14}{'IC':>8}")
        for H, m in res.items():
            print(f"    {H:>8}{m['n']:>8}{100*m['hit']:>7.1f}%"
                  f"{m['edge_ticks']:>14.2f}{m['ic']:>8.3f}")


def _aggregate(streams, signal_fn):
    """Average metrics across several streams (for the synthetic multi-seed sets)."""
    accs = {H: {"hit": [], "edge_ticks": [], "ic": [], "n": 0} for H in HORIZONS}
    for st in streams:
        r = analyze(st, signal_fn)
        for H in HORIZONS:
            accs[H]["hit"].append(r[H]["hit"])
            accs[H]["edge_ticks"].append(r[H]["edge_ticks"])
            accs[H]["ic"].append(r[H]["ic"])
            accs[H]["n"] += r[H]["n"]
    return {H: {"n": accs[H]["n"],
                "hit": stats.fmean(accs[H]["hit"]),
                "edge_ticks": stats.fmean(accs[H]["edge_ticks"]),
                "ic": stats.fmean(accs[H]["ic"])} for H in HORIZONS}


def _chart(datasets, out_path):
    """datasets: list of (label, {H: metrics}) for the OBI signal. Plots hit% and IC."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5))
    for label, res in datasets:
        hs = list(res)
        ax1.plot(hs, [100 * res[H]["hit"] for H in hs], marker="o", label=label)
        ax2.plot(hs, [res[H]["ic"] for H in hs], marker="o", label=label)
    ax1.axhline(50, color="#787878", ls=":", lw=1, label="coin flip (no edge)")
    ax1.set_title("Hit rate vs horizon (OBI signal)", color="#0d47a1")
    ax1.set_xlabel("horizon (ticks)"); ax1.set_ylabel("hit rate (%)"); ax1.legend(fontsize=8)
    ax2.axhline(0, color="#787878", ls=":", lw=1)
    ax2.set_title("Information coefficient vs horizon", color="#0d47a1")
    ax2.set_xlabel("horizon (ticks)"); ax2.set_ylabel("IC"); ax2.legend(fontsize=8)
    fig.suptitle("Signal Alpha Analysis", fontsize=13, color="#0d47a1", fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(out_path, dpi=110)
    print(f"\nchart written to {out_path}")


def main():
    ap = argparse.ArgumentParser(description="does the signal predict? alpha analysis")
    ap.add_argument("--n", type=int, default=6000)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--real-log", default=None,
                    help="also analyze real quotes from a live-run log")
    reports = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "reports"))
    ap.add_argument("--out", default=os.path.join(reports, "alpha_analysis.png"))
    args = ap.parse_args()

    seeds = list(range(1, args.seeds + 1))
    rnd_streams = [stream_random(args.n, s) for s in seeds]
    inj_streams = [stream_injected(args.n, s) for s in seeds]

    rnd = {"OBI": _aggregate(rnd_streams, obi), "MPX": _aggregate(rnd_streams, mpx)}
    inj = {"OBI": _aggregate(inj_streams, obi), "MPX": _aggregate(inj_streams, mpx)}
    _print_block("RANDOM WALK (null — expect ~50% hit, IC~0)", rnd)
    _print_block("INJECTED ALPHA (control — expect high hit, IC>0)", inj)

    chart_sets = [("random walk", rnd["OBI"]), ("injected alpha", inj["OBI"])]

    if args.real_log:
        real = real_stream_ticks(args.real_log)
        if len(real) > max(HORIZONS) + 2:
            res = {"OBI": analyze(real, obi), "MPX": analyze(real, mpx)}
            _print_block(f"REAL DATA ({os.path.basename(args.real_log)}, "
                         f"{len(real)} quotes)", res)
            print("  caveat: real quotes sampled at signal moments (biased, AAPL-only)")
            chart_sets.append(("real AAPL", res["OBI"]))
        else:
            print(f"\n(real log {args.real_log}: too few quotes to analyze)")

    _chart(chart_sets, args.out)

    print("\nreading: hit rate ~50% and IC ~0 mean NO short-horizon edge. The injected"
          "\ncontrol should show a clearly higher hit rate — if it does, the tool is"
          "\nsound and any flat result on other data is a real finding, not a bug.")


if __name__ == '__main__':
    main()
