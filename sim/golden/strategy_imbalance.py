"""
Strategy Golden Model — faithful mirror of rtl/strategy_imbalance.sv (M2 / B3).

This REPLACES host/strategy_sw.py as the reference for the current RTL.
strategy_sw.py models the M1-era single-level strategy (best bid/ask size only,
fixed 100-share lot, no spread guard). The RTL evolved in B3 to:

  * DEPTH-WEIGHTED volumes: w = Σ (NLEVELS - i) * level_size[i] over the top
    NLEVELS levels — the touch (level 0) weighted heaviest.
  * SPREAD GUARD: only fire on a normal book (ask > bid) with
    spread <= MAX_SPREAD_TICKS.
  * IMBALANCE-SCALED lot sizing: 1x / 2x / 3x BASE_LOT, capped at MAX_LOT.
  * prev_valid 2-consecutive-cycle gate (this part matches strategy_sw.py).
  * COOLDOWN of N clock cycles after a fire — a *cycle-level* effect. In the
    per-message replay model each message is separated by many feed cycles
    (>> COOLDOWN_CYCLES), so cooldown never spans messages; the per-message
    sampling collapses any within-window repeats anyway. It is therefore not
    modelled here and is validated at cycle granularity in Phase 3.

Decision pricing matches the RTL exactly: BUY lifts the ask (order_price =
best_ask), SELL hits the bid (order_price = best_bid).
"""

from dataclasses import dataclass


@dataclass
class Decision:
    action: int    # 0 = BUY, 1 = SELL
    price: int
    size: int


class StrategyImbalance:
    """Mirror of strategy_imbalance.sv (cooldown excluded — see module docstring)."""

    def __init__(self, nlevels=4, bid_thresh=15, ask_thresh=15,
                 max_spread_ticks=1000, base_lot=100, max_lot=250):
        self.N = nlevels
        self.BID_THRESH = bid_thresh
        self.ASK_THRESH = ask_thresh
        self.MAX_SPREAD_TICKS = max_spread_ticks
        self.BASE_LOT = base_lot
        self.MAX_LOT = max_lot
        self._prev_valid = False

    def _weighted(self, level_size: list) -> int:
        # weight (N - i) for level i: touch = N, deepest tracked = 1
        return sum((self.N - i) * level_size[i] for i in range(self.N))

    def _lot(self, dom: int, oth: int, thresh: int) -> int:
        # dom/oth = dominant/other weighted volumes (same cross-multiply form)
        if dom * 10 > 3 * thresh * oth:
            size = 3 * self.BASE_LOT
        elif dom * 10 > 2 * thresh * oth:
            size = 2 * self.BASE_LOT
        else:
            size = self.BASE_LOT
        return min(size, self.MAX_LOT)

    def evaluate(self, snap) -> Decision | None:
        """One evaluation per book update (snap = BookSnapshot)."""
        normal_book = snap.best_ask_price > snap.best_bid_price
        spread = (snap.best_ask_price - snap.best_bid_price) if normal_book else 0
        elig = (snap.book_valid and self._prev_valid and normal_book
                and spread <= self.MAX_SPREAD_TICKS)
        self._prev_valid = snap.book_valid

        if not elig:
            return None

        w_bid = self._weighted(snap.bid_level_size)
        w_ask = self._weighted(snap.ask_level_size)

        buy_cond = (w_bid * 10 > self.BID_THRESH * w_ask) and (snap.best_ask_price > 0)
        sell_cond = (w_ask * 10 > self.ASK_THRESH * w_bid) and (snap.best_bid_price > 0)

        if buy_cond:                       # BUY checked first (RTL priority)
            return Decision(0, snap.best_ask_price, self._lot(w_bid, w_ask, self.BID_THRESH))
        if sell_cond:
            return Decision(1, snap.best_bid_price, self._lot(w_ask, w_bid, self.ASK_THRESH))
        return None

    def reset(self):
        self._prev_valid = False
