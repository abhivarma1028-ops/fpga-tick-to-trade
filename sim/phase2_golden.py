"""
Phase-2 golden trace generator (pure Python, no simulator).

Runs a replay ITCH stream through the full golden chain
    parser  ->  M2 RESCAN order book  ->  imbalance strategy  ->  risk_check
and emits the expected decision trace. This is:
  (a) a standalone sanity check that the golden pipeline produces sensible
      decisions on a large realistic stream, and
  (b) the reference trace that tb_replay_regression.py diffs the RTL against.

The RiskCheck class mirrors rtl/risk_check.sv exactly (same params, same
priority-encoded reasons, registered position that moves only on accepted
orders). It is NOT risk_guard.py — that one adds a wall-clock rate limit the
RTL has no concept of.

Run:
    cd sim && python phase2_golden.py [n_msgs] [seed]
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'host'))

from golden.itch_parser import parse_stream
from golden.order_book_m2 import OrderBookM2
from golden.strategy_imbalance import StrategyImbalance
import replay_gen


# reason codes (match risk_check.sv localparams)
R_OK, R_SIZE, R_PRICE, R_POSITION, R_HALT = 0, 1, 2, 3, 4


class RiskCheck:
    """Faithful mirror of rtl/risk_check.sv."""

    def __init__(self, max_order_size=500, max_position=1000, max_price_band=5000):
        self.MAX_ORDER_SIZE = max_order_size
        self.MAX_POSITION = max_position
        self.MAX_PRICE_BAND = max_price_band
        self.position = 0          # registered net position

    def check(self, action, price, size, ref_price, halt=False):
        """Returns (out_valid, reject_reason). Updates position on accept."""
        price_dev = abs(price - ref_price)
        signed_qty = -size if action else size
        new_position = self.position + signed_qty

        ok_size = size <= self.MAX_ORDER_SIZE
        ok_price = price_dev <= self.MAX_PRICE_BAND
        ok_position = abs(new_position) <= self.MAX_POSITION

        out_valid = (not halt) and ok_size and ok_price and ok_position

        if halt:            reason = R_HALT
        elif not ok_size:   reason = R_SIZE
        elif not ok_price:  reason = R_PRICE
        elif not ok_position: reason = R_POSITION
        else:               reason = R_OK

        if out_valid:
            self.position = new_position      # only accepted orders move it
        return out_valid, reason


def run(stream: bytes):
    """Returns (decisions, rejects, stats). One evaluation per message."""
    msgs = parse_stream(stream)
    book = OrderBookM2()
    strat = StrategyImbalance()
    risk = RiskCheck()

    decisions = []   # accepted: list of (msg_index, action, price, size)
    rejects = []     # blocked:  list of (msg_index, reason)

    for i, m in enumerate(msgs):
        book.apply(m)
        s = book.snapshot()
        dec = strat.evaluate(s)
        if dec is None:
            continue
        mid = (s.best_bid_price + s.best_ask_price) >> 1
        ok, reason = risk.check(dec.action, dec.price, dec.size, mid)
        if ok:
            decisions.append((i, dec.action, dec.price, dec.size))
        else:
            rejects.append((i, reason))

    stats = {
        'messages': len(msgs),
        'accepted': len(decisions),
        'rejected': len(rejects),
        'final_position': risk.position,
    }
    return decisions, rejects, stats


if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    stream = replay_gen.build_synthetic(n, seed)
    decisions, rejects, stats = run(stream)

    print(f"=== Phase-2 golden trace (n={n}, seed={seed}) ===")
    print(f"  ITCH messages parsed : {stats['messages']}")
    print(f"  accepted decisions   : {stats['accepted']}")
    print(f"  risk rejects         : {stats['rejected']}")
    print(f"  final net position   : {stats['final_position']} shares")

    reason_names = {R_SIZE: 'SIZE', R_PRICE: 'PRICE', R_POSITION: 'POSITION', R_HALT: 'HALT'}
    if rejects:
        from collections import Counter
        c = Counter(r for _, r in rejects)
        print("  reject breakdown     : " +
              ", ".join(f"{reason_names.get(k, k)}={v}" for k, v in sorted(c.items())))

    print("\n  first 12 accepted decisions:")
    for i, (mi, a, p, sz) in enumerate(decisions[:12]):
        print(f"    msg#{mi:<5} {'BUY ' if a == 0 else 'SELL'} price={p} size={sz}")
