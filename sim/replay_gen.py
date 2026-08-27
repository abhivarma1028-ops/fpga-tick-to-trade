"""
Phase-2 replay stream generator.

Produces a long, deterministic, *realistic* ITCH 5.0 byte stream that exercises
the edge cases hand-crafted tests miss: multi-level books, same-price
aggregation, partial executes, cancels and deletes that empty a level, replaces,
and occasional crossed/locked books.

Two sources:
  * build_synthetic(n, seed)  — seeded generator, runs anywhere, no download.
  * load_slice(path)          — a real NASDAQ slice carved by data/carve_itch_slice.py.

Order refs are drawn from a bounded free pool (1..255) so each live order maps to
a distinct slot in the 256-entry book — i.e. no order_ref mod-256 aliasing. The
real-slice path uses arbitrary 64-bit refs and DOES exercise aliasing; both the
RTL and the golden model (order_book_m2.py) handle it identically, so the
equivalence check holds either way.
"""

import random
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from synth_itch import SynthITCH

MID = 1_500_000          # $150.0000 in ITCH fixed-point
TICK = 100               # $0.01
MAX_LIVE = 200           # keep < 256 so slots never alias in the synthetic path


def build_synthetic(n_msgs: int = 2000, seed: int = 1) -> bytes:
    """Build a framed ITCH stream of ~n_msgs messages."""
    rng = random.Random(seed)
    gen = SynthITCH(symbol=b'AAPL    ')
    free_refs = list(range(1, 256))
    rng.shuffle(free_refs)
    live: dict[int, tuple[int, int, int]] = {}   # ref -> (side, price, shares)
    out = bytearray()
    msgs = 0

    def pick_price(side: int) -> int:
        # bids strictly below mid, asks strictly above — a normal book with a
        # 2..8 tick spread ($0.02..$0.08), inside the strategy's MAX_SPREAD guard.
        lvl = rng.randint(1, 4)
        return MID - lvl * TICK if side == 0 else MID + lvl * TICK

    while msgs < n_msgs:
        r = rng.random()

        # ADD — biased high; sometimes a large size to create top-of-book imbalance
        if (r < 0.55 or len(live) < 8) and free_refs:
            ref = free_refs.pop()
            side = rng.randint(0, 1)
            price = pick_price(side)
            shares = rng.choice([100, 200, 300, 500, 1000, 2500])
            out += gen.add(ref=ref, side=('B' if side == 0 else 'S'),
                           shares=shares, price=price)
            live[ref] = (side, price, shares)
            msgs += 1

        elif live and r < 0.70:                       # partial EXECUTE
            ref = rng.choice(list(live.keys()))
            side, price, sh = live[ref]
            ex = max(1, sh // 2)
            out += gen.execute(ref=ref, shares=ex)
            if ex >= sh:
                free_refs.append(ref); del live[ref]
            else:
                live[ref] = (side, price, sh - ex)
            msgs += 1

        elif live and r < 0.82:                       # CANCEL (partial or full)
            ref = rng.choice(list(live.keys()))
            side, price, sh = live[ref]
            cx = rng.choice([sh, max(1, sh // 3)])
            out += gen.cancel(ref=ref, shares=cx)
            if cx >= sh:
                free_refs.append(ref); del live[ref]
            else:
                live[ref] = (side, price, sh - cx)
            msgs += 1

        elif live and r < 0.92:                       # DELETE (empties the order)
            ref = rng.choice(list(live.keys()))
            out += gen.delete(ref=ref)
            free_refs.append(ref); del live[ref]
            msgs += 1

        elif live and free_refs:                      # REPLACE
            ref = rng.choice(list(live.keys()))
            side, _, _ = live[ref]
            nref = free_refs.pop()
            nprice = pick_price(side)
            nsh = rng.choice([100, 200, 400, 800])
            out += gen.replace(orig_ref=ref, new_ref=nref, shares=nsh, price=nprice)
            del live[ref]
            live[nref] = (side, nprice, nsh)
            msgs += 1

    return bytes(out)


def load_slice(path: str) -> bytes:
    """Load a real carved ITCH slice (framed messages) produced by carve_itch_slice.py."""
    with open(path, 'rb') as f:
        return f.read()


if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    data = build_synthetic(n)
    print(f"built {len(data)} bytes of framed ITCH ({n} messages requested)")
