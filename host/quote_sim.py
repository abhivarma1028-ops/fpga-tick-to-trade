"""
Shared synthetic live-quote generator.

Used by both the live prototype (live_feed.py --simulate) and the FPGA-vs-software
shadow logger (sim/tb_shadow_quotes.py), so the shadow report is computed on the
EXACT stream the prototype trades on. Random-walk mid + fluctuating top-of-book
sizes with occasional imbalance bursts (the conditions the strategy fires on).
"""

import random

MID0 = 150.00
HALF_SPREAD = 0.025


def gen_quotes(n: int, seed: int = 1):
    """Return n L1 quotes as (bid_usd, bid_size, ask_usd, ask_size)."""
    rng = random.Random(seed)
    mid = MID0
    out = []
    for _ in range(n):
        mid += rng.choice([-0.02, -0.01, 0.0, 0.0, 0.01, 0.02])
        mid = max(50.0, mid)
        bid = round(mid - HALF_SPREAD, 2)
        ask = round(mid + HALF_SPREAD, 2)
        bs = rng.randint(80, 300)
        asz = rng.randint(80, 300)
        if rng.random() < 0.16:                  # ~1-in-6 imbalance burst
            if rng.random() < 0.5:
                bs = rng.randint(800, 2500)      # bid-heavy  -> BUY
            else:
                asz = rng.randint(800, 2500)     # ask-heavy  -> SELL
        out.append((bid, bs, ask, asz))
    return out
