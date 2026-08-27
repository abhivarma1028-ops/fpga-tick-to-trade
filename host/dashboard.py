"""
Results dashboard — renders the trade brain's output as charts.

Three panels, saved to reports/dashboard.png:

  1. SW-vs-FPGA latency distribution — histogram of software tick-to-signal
     compute time, with the FPGA's fixed 205 ns marked, plus median / p99 and
     the resulting speedup. This is the project's headline story.
  2. Pre-trade funnel — how many raw signals each gate vetoed, down to orders.
  3. Equity curve — portfolio PnL over the sequence of fills.

Data sources (both optional; the script uses whatever is present):
  * reports/run_summary.json  — written by host/live_feed.py at the end of a run
    (funnel, equity curve, latency samples).
  * a text log (default reports/soak_aapl.log) — real live-Alpaca run; its
    "sw compute N ns" lines give a large real-data latency sample. Preferred for
    the latency panel when available (--latency-log).

Run:
    # from a fresh sim (writes run_summary.json first):
    python live_feed.py --universe mag7 --simulate --duration 5 && python dashboard.py
    # or point at the real soak log for the latency panel:
    python dashboard.py --latency-log ../reports/soak_aapl.log
"""

import os
import re
import json
import argparse
import statistics as stats

import matplotlib
matplotlib.use("Agg")            # headless: save PNGs, no display needed
import matplotlib.pyplot as plt

HERE = os.path.dirname(__file__)
REPORTS = os.path.normpath(os.path.join(HERE, '..', 'reports'))

# funnel reason -> readable label, in gauntlet order
FUNNEL_ORDER = [
    ("fair_value", "fair value"),
    ("edge_vs_cost", "edge vs cost"),
    ("volatility", "volatility"),
    ("liquidity", "liquidity"),
    ("staleness", "staleness"),
    ("inventory_skew", "inventory skew"),
    ("gross_exposure", "gross exposure"),
    ("kill_switch", "kill switch"),
]

_LAT_RE = re.compile(r"sw compute\s+(\d+)\s*ns")


def load_summary():
    path = os.path.join(REPORTS, 'run_summary.json')
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    return None


def latencies_from_log(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, errors='ignore') as fh:
        return [int(m.group(1)) for line in fh for m in [_LAT_RE.search(line)] if m]


def _fmt_ns(ns):
    return f"{ns/1000:.1f} us" if ns >= 1000 else f"{ns:.0f} ns"


def panel_latency(ax, lat, fpga_ns, source):
    if not lat:
        ax.set_title("latency — no data"); ax.axis('off'); return
    med = stats.median(lat)
    p99 = sorted(lat)[min(len(lat) - 1, int(0.99 * len(lat)))]
    ax.hist([x / 1000 for x in lat], bins=40, color="#1b5e20", alpha=0.75,
            edgecolor="white", linewidth=0.3)
    ax.axvline(fpga_ns / 1000, color="#b71c1c", linewidth=2,
               label=f"FPGA {fpga_ns} ns (fixed)")
    ax.axvline(med / 1000, color="#0d47a1", linestyle="--", linewidth=1.5,
               label=f"SW median {_fmt_ns(med)}")
    ax.set_title(f"Tick-to-signal latency  (SW vs FPGA)  ·  n={len(lat)}  [{source}]",
                 fontsize=10, color="#0d47a1")
    ax.set_xlabel("microseconds (us)"); ax.set_ylabel("count")
    speedup = med / fpga_ns
    ax.text(0.98, 0.55, f"median speedup\nFPGA is ~{speedup:.0f}x faster\n"
            f"SW p99 {_fmt_ns(p99)}", transform=ax.transAxes, ha="right",
            fontsize=9, bbox=dict(boxstyle="round", fc="#fff9c4", ec="#ffc107"))
    ax.legend(fontsize=8, loc="upper right")


def panel_funnel(ax, funnel):
    if not funnel:
        ax.set_title("funnel — no data"); ax.axis('off'); return
    passed = funnel.get("passed", 0)
    labels, counts = [], []
    for key, label in FUNNEL_ORDER:
        if funnel.get(key):
            labels.append(f"veto: {label}"); counts.append(funnel[key])
    labels.append("ORDERS PASSED"); counts.append(passed)
    colors = ["#b71c1c"] * (len(labels) - 1) + ["#1b5e20"]
    total = sum(counts)
    y = range(len(labels))
    ax.barh(list(y), counts, color=colors, alpha=0.8)
    ax.set_yticks(list(y)); ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    for i, c in enumerate(counts):
        ax.text(c, i, f" {c}", va="center", fontsize=8)
    ax.set_title(f"Pre-trade funnel  ·  {total} raw signals -> {passed} orders",
                 fontsize=10, color="#0d47a1")
    ax.set_xlabel("signals")


def panel_pnl_by_symbol(ax, books):
    """Per-symbol total PnL (realized + unrealized). Shown when there is no
    funnel (e.g. a market-maker run, which bypasses the gauntlet)."""
    if not books:
        ax.set_title("per-symbol PnL — no data"); ax.axis('off'); return
    syms = sorted(books)
    pnl = [books[s].get("realized", 0) + books[s].get("unrealized", 0) for s in syms]
    colors = ["#1b5e20" if v >= 0 else "#b71c1c" for v in pnl]
    ax.barh(range(len(syms)), pnl, color=colors, alpha=0.8)
    ax.set_yticks(range(len(syms))); ax.set_yticklabels(syms, fontsize=8)
    ax.invert_yaxis()
    ax.axvline(0, color="#787878", linewidth=0.8)
    for i, v in enumerate(pnl):
        ax.text(v, i, f" {v:+.1f}", va="center", fontsize=8)
    ax.set_title(f"Per-symbol PnL (USD)  ·  total {sum(pnl):+,.2f}",
                 fontsize=10, color="#0d47a1")
    ax.set_xlabel("PnL (USD)")


def panel_equity(ax, curve):
    if not curve:
        ax.set_title("equity — no fills"); ax.axis('off'); return
    ax.plot(range(1, len(curve) + 1), curve, color="#0d47a1", linewidth=1.4)
    ax.axhline(0, color="#787878", linewidth=0.8, linestyle=":")
    final = curve[-1]
    peak = max(curve); trough = min(curve)
    ax.fill_between(range(1, len(curve) + 1), curve, 0,
                    color="#1b5e20" if final >= 0 else "#b71c1c", alpha=0.12)
    ax.set_title(f"Portfolio equity over fills  ·  final {final:,.2f} USD  "
                 f"(peak {peak:,.0f} / trough {trough:,.0f})",
                 fontsize=10, color="#0d47a1")
    ax.set_xlabel("fill #"); ax.set_ylabel("PnL (USD)")


def main():
    ap = argparse.ArgumentParser(description="render the trade-brain dashboard")
    ap.add_argument("--latency-log", default=os.path.join(REPORTS, "soak_aapl.log"),
                    help="text log to mine 'sw compute N ns' from (real-data latency)")
    ap.add_argument("--out", default=os.path.join(REPORTS, "dashboard.png"))
    args = ap.parse_args()

    summary = load_summary()

    # Prefer the real soak-log latencies; fall back to the run's own samples.
    log_lat = latencies_from_log(args.latency_log)
    if log_lat:
        lat, src = log_lat, os.path.basename(args.latency_log)
    elif summary and summary.get("latency_ns"):
        lat, src = summary["latency_ns"], "sim run"
    else:
        lat, src = [], "none"

    fpga_ns = (summary or {}).get("fpga_ns", 205)
    funnel = (summary or {}).get("funnel", {})
    curve = (summary or {}).get("equity_curve", [])
    books = (summary or {}).get("books", {})

    fig, axes = plt.subplots(3, 1, figsize=(9, 12))
    fig.suptitle("HFT Trade-Brain Dashboard", fontsize=14, color="#0d47a1",
                 fontweight="bold")
    panel_latency(axes[0], lat, fpga_ns, src)
    # middle panel: the gauntlet funnel (taker) or per-symbol PnL (maker)
    if funnel:
        panel_funnel(axes[1], funnel)
    else:
        panel_pnl_by_symbol(axes[1], books)
    panel_equity(axes[2], curve)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(args.out, dpi=110)
    print(f"dashboard written to {args.out}")

    # also a short text digest
    if lat:
        med = stats.median(lat)
        print(f"  latency: n={len(lat)}  median={_fmt_ns(med)}  "
              f"FPGA={fpga_ns}ns  speedup=~{med/fpga_ns:.0f}x  [{src}]")
    if funnel:
        print(f"  funnel : {sum(funnel.values())} signals -> "
              f"{funnel.get('passed', 0)} orders")
    if curve:
        print(f"  equity : {len(curve)} fills, final ${curve[-1]:,.2f}")


if __name__ == '__main__':
    main()
