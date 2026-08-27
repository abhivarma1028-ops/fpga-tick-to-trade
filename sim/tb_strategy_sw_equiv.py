"""
Equivalence test: host/strategy_sw.py  ==  sim/golden/strategy_imbalance.py

Both are software mirrors of rtl/strategy_imbalance.sv (M2/B3, depth-weighted):
  * golden/strategy_imbalance.py  drives the cocotb RTL regression (the reference
    the hardware is diffed against).
  * host/strategy_sw.py           is the model the LIVE trading path executes.

If these two ever disagree, the live software path would place different orders
than the FPGA/RTL would on the same book — i.e. the "dual-path equivalence"
claim silently breaks. This test drives BOTH from the identical book-snapshot
stream (parser -> M2 order book -> snapshot) and asserts decision-for-decision
equality (fires on the same messages, same action/price/size, same None gaps).

The host model takes flat args + optional level lists; the golden model takes a
BookSnapshot. We feed both from the same snapshot so the only thing under test
is the decision logic, not the book.

Run:
    cd sim && python tb_strategy_sw_equiv.py            # default sweep
    cd sim && python tb_strategy_sw_equiv.py 5000 42    # single (n, seed)
Exit code 0 = all equivalent, 1 = a mismatch was found (CI-friendly).
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'host'))

from golden.itch_parser import parse_stream
from golden.order_book_m2 import OrderBookM2
from golden.strategy_imbalance import StrategyImbalance   # reference (cocotb)
from strategy_sw import SoftwareStrategy                  # live software path
import replay_gen


def _dec_tuple(dec):
    """Normalise either Decision type (or None) to a comparable tuple."""
    if dec is None:
        return None
    return (dec.action, dec.price, dec.size)


def run_one(n, seed, params=None):
    """Drive both models over one synthetic stream.

    Returns (n_msgs, n_fires, mismatches) where mismatches is a list of
    (msg_index, golden_tuple, host_tuple).
    """
    params = params or {}
    stream = replay_gen.build_synthetic(n, seed)
    msgs = parse_stream(stream)

    book = OrderBookM2()
    gold = StrategyImbalance(**params)
    # cooldown_ticks defaults to 0 so the host model matches the golden model,
    # which does not model the cycle-level cooldown (see both docstrings).
    host = SoftwareStrategy(cooldown_ticks=0, **params)

    mismatches = []
    n_fires = 0

    for i, m in enumerate(msgs):
        book.apply(m)
        s = book.snapshot()

        g = gold.evaluate(s)
        h = host.evaluate(
            book_valid=s.book_valid,
            best_bid_price=s.best_bid_price, best_bid_size=s.best_bid_size,
            best_ask_price=s.best_ask_price, best_ask_size=s.best_ask_size,
            bid_level_sizes=s.bid_level_size, ask_level_sizes=s.ask_level_size,
        )

        gt, ht = _dec_tuple(g), _dec_tuple(h)
        if gt is not None:
            n_fires += 1
        if gt != ht:
            mismatches.append((i, gt, ht))

    return len(msgs), n_fires, mismatches


def main():
    if len(sys.argv) > 1:
        cases = [(int(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 1)]
    else:
        # sweep a range of sizes and seeds to exercise many book states
        cases = [(n, seed) for n in (500, 2000, 8000) for seed in range(1, 6)]

    print("=== strategy_sw.py  vs  golden/strategy_imbalance.py equivalence ===")
    total_msgs = total_fires = total_mismatch = 0
    failed = False

    for n, seed in cases:
        n_msgs, n_fires, mismatches = run_one(n, seed)
        total_msgs += n_msgs
        total_fires += n_fires
        total_mismatch += len(mismatches)
        status = "OK " if not mismatches else "FAIL"
        print(f"  [{status}] n={n:<5} seed={seed}: {n_msgs:>6} msgs, "
              f"{n_fires:>4} golden fires, {len(mismatches)} mismatch")
        if mismatches:
            failed = True
            for mi, gt, ht in mismatches[:5]:
                print(f"          msg#{mi}: golden={gt}  host={ht}")
            if len(mismatches) > 5:
                print(f"          ... +{len(mismatches) - 5} more")

    print(f"\n  totals: {total_msgs} msgs, {total_fires} decisions, "
          f"{total_mismatch} mismatches across {len(cases)} streams")
    if failed:
        print("  RESULT: MISMATCH — the live software path diverges from the "
              "golden/RTL reference.")
        sys.exit(1)
    print("  RESULT: EQUIVALENT — strategy_sw.py reproduces the golden model "
          "decision-for-decision.")


if __name__ == '__main__':
    main()
