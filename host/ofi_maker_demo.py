"""
Validation demo for the OFI-driven maker (host/ofi_maker.py).

Shows WHEN the OFI tilt helps and when it doesn't — the honest conditions:

  1. Structureless random walk + flat fills  -> OFI tilt is noise (no signal).
  2. OFI-predictive feed + spread-aware fills -> OFI-maker dominates: the plain
     maker LOSES to adverse selection on trends, while the OFI-maker leans into
     the predicted move (a more aggressive quote fills the winning side more) and
     profits. This is the whole point of "predict + quote".

The two conditions that make it work: the signal must be real (mid follows order
flow), AND fills must be price-sensitive (a keener quote fills more) — both true
in real markets. Run: python host/ofi_maker_demo.py
"""
import random
import statistics as st
from ofi_maker import OFIMaker, OFIMakerConfig, ofi_increment
from market_maker import MarketMaker, MMConfig
from portfolio import Portfolio

USD = 10000
SEEDS = [1, 2, 3, 7, 11]


def feed_randomwalk(n, seed):
    rng = random.Random(seed); mid = 1_000_000; out = []
    for _ in range(n):
        bs = rng.randint(80, 300); asz = rng.randint(80, 300)
        if rng.random() < 0.35:
            if rng.random() < 0.5: bs += rng.randint(600, 3000)
            else:                  asz += rng.randint(600, 3000)
        out.append((mid - 100, bs, mid + 100, asz))
        mid += rng.choice([-100, 0, 100]); mid = max(500_000, mid)
    return out


def feed_ofi_predictive(n, seed, strength=3):
    """Mid drifts in the direction of accumulated OFI -> the signal is REAL."""
    rng = random.Random(seed); mid = 1_000_000; prev = None; buf = [0]*8; out = []
    for _ in range(n):
        bs = rng.randint(80, 300); asz = rng.randint(80, 300)
        if rng.random() < 0.35:
            if rng.random() < 0.5: bs += rng.randint(600, 3000)
            else:                  asz += rng.randint(600, 3000)
        cur = (mid - 100, bs, mid + 100, asz)
        if prev is not None: buf = buf[1:] + [ofi_increment(prev, cur)]
        prev = cur; ofi = sum(buf); out.append(cur)
        mid += strength * (1 if ofi > 0 else -1 if ofi < 0 else 0) * 100 + rng.choice([-100, 0, 100])
        mid = max(500_000, mid)
    return out


def run(maker, stream, seed, spread_aware):
    pf = Portfolio(); rng = random.Random(seed); fees = 0.0; sym = "SIM"
    def fp(qpx, is_bid, mid):
        if not spread_aware: return 0.25
        dist = (mid - qpx) if is_bid else (qpx - mid)
        return max(0.02, min(0.6, 0.35 - dist / 2000.0))
    for i in range(len(stream) - 1):
        bp, bs, ap, asz = stream[i]; mid = (bp + ap) // 2; pf.mark_price(sym, mid)
        inv = pf.books[sym].pos if sym in pf.books else 0
        q = maker.quote(bp, bs, ap, asz, inv); nbp, _, nap, _ = stream[i + 1]
        if q.bid_size and q.bid_price and rng.random() < fp(q.bid_price, True, mid):
            pf.on_fill(sym, 0, q.bid_size, q.bid_price); fees += (1/1e4)*(q.bid_price/USD)*q.bid_size
        if q.ask_size and q.ask_price and rng.random() < fp(q.ask_price, False, mid):
            pf.on_fill(sym, 1, q.ask_size, q.ask_price); fees += (1/1e4)*(q.ask_price/USD)*q.ask_size
        pf.mark_price(sym, (nbp + nap) // 2)
    return pf.equity() - fees


def compare(label, feed_fn, spread_aware):
    plain = st.fmean([run(MarketMaker(MMConfig(half_spread_ticks=200)), feed_fn(4000, s), s, spread_aware) for s in SEEDS])
    ofim = st.fmean([run(OFIMaker(OFIMakerConfig(half_spread=200)), feed_fn(4000, s), s, spread_aware) for s in SEEDS])
    print(f"\n{label}")
    print(f"  plain maker     : ${plain:>9.0f}")
    print(f"  OFI-driven maker: ${ofim:>9.0f}   ({'+' if ofim > plain else ''}{ofim - plain:.0f} vs plain)")


if __name__ == "__main__":
    print("=== OFI-driven maker: when does the tilt help? ===")
    compare("1) random walk + flat fills  (no signal -> tilt is noise)",
            feed_randomwalk, spread_aware=False)
    compare("2) OFI-predictive feed + spread-aware fills  (signal real -> tilt wins)",
            feed_ofi_predictive, spread_aware=True)
    print("\nTakeaway: the OFI tilt pays only when the signal is real AND fills are")
    print("price-sensitive (both true in real markets); on a structureless walk it just adds noise.")
