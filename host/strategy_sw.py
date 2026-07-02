"""
Software mirror of rtl/strategy_imbalance.sv  (M2 / B3 — depth-weighted).

Faithful to the current RTL so the live software path produces the same
decisions the FPGA would on the same inputs:

  * DEPTH-WEIGHTED volumes: w = Σ (NLEVELS - i) * level_size[i] over the top
    NLEVELS levels — the touch (level 0) weighted heaviest, deepest level = 1.
  * Cross-multiply trigger (no division):
        BUY : w_bid * 10 > BID_THRESH * w_ask   &&  best_ask_price > 0
        SELL: w_ask * 10 > ASK_THRESH * w_bid   &&  best_bid_price > 0
  * SPREAD GUARD: only fire on a normal book (ask > bid) with
        spread <= MAX_SPREAD_TICKS.
  * prev_valid 2-consecutive-evaluation gate.
  * IMBALANCE-SCALED lot sizing: 1x / 2x / 3x BASE_LOT, capped at MAX_LOT.
  * Aggressive pricing: BUY lifts the ask, SELL hits the bid.

NOTE — history. The previous version of this file modelled the *M1* strategy
(single best bid/ask size, fixed 100-share lot, no spread guard). The RTL grew
the depth-weighting / spread guard / cooldown / lot-sizing in B3; this file was
stale and the live shadow path would have disagreed with the FPGA. Brought back
in line 2026-06-16. The cocotb regression mirror is sim/golden/strategy_imbalance.py
— keep the two in sync if the RTL changes.

NOTE — cooldown. The RTL suppresses new decisions for COOLDOWN_CYCLES (8) clock
cycles (= 40 ns at 200 MHz) after a fire. On a live feed, market updates are
milliseconds apart — far longer than 40 ns — so the hardware cooldown never
spans two live ticks. It is therefore expressed here in *evaluations* and
defaults to 0 (inactive), which is what reproduces the FPGA's behaviour at live
tick rates. Set cooldown_ticks > 0 only to rate-limit the software path itself.

NOTE — depth on the live path. Live IBKR L1 quotes carry only the touch
(level 0). With levels 1..N-1 absent the depth-weighted formula reduces exactly
to the single-level threshold (w = N * size_0 on both sides cancels the N), so
the trigger matches the old behaviour while additionally applying the spread
guard and lot sizing. Supplying real depth (when available) makes it match the
FPGA's multi-level view.

Fixed-point convention (matches the RTL and ITCH):
    price is an integer in units of 1/10,000 USD  (i.e. price_usd * 10_000)
    size  is an integer share count
"""

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass
class Decision:
    action: int       # 0 = BUY, 1 = SELL
    price:  int       # order limit price, ITCH fixed-point (price_usd * 10_000)
    size:   int       # shares


class SoftwareStrategy:
    """Mirrors strategy_imbalance.sv (cooldown in evaluations; see module docstring)."""

    def __init__(self, nlevels: int = 4, bid_thresh: int = 15, ask_thresh: int = 15,
                 max_spread_ticks: int = 1000, base_lot: int = 100,
                 max_lot: int = 250, cooldown_ticks: int = 0):
        self.N = nlevels
        self.BID_THRESH = bid_thresh
        self.ASK_THRESH = ask_thresh
        self.MAX_SPREAD_TICKS = max_spread_ticks
        self.BASE_LOT = base_lot
        self.MAX_LOT = max_lot
        self.COOLDOWN = cooldown_ticks
        self._prev_valid = False
        self._cooldown = 0

    # -- helpers (mirror the RTL functions) ---------------------------------
    def _weighted(self, level_sizes: Sequence[int]) -> int:
        # weight (N - i) for level i: touch = N, deepest tracked level = 1
        return sum((self.N - i) * (level_sizes[i] if i < len(level_sizes) else 0)
                   for i in range(self.N))

    def _lot(self, dom: int, oth: int, thresh: int) -> int:
        if dom * 10 > 3 * thresh * oth:
            size = 3 * self.BASE_LOT
        elif dom * 10 > 2 * thresh * oth:
            size = 2 * self.BASE_LOT
        else:
            size = self.BASE_LOT
        return min(size, self.MAX_LOT)

    # -----------------------------------------------------------------------
    def evaluate(self, book_valid: bool,
                 best_bid_price: int, best_bid_size: int,
                 best_ask_price: int, best_ask_size: int,
                 bid_level_sizes: Optional[Sequence[int]] = None,
                 ask_level_sizes: Optional[Sequence[int]] = None) -> Optional[Decision]:
        """One evaluation per market-data update.

        bid_level_sizes / ask_level_sizes are the top-N level sizes (level 0
        first). When omitted (live L1 path), only the touch size is used.
        Returns a Decision when the RTL would fire, else None.
        """
        if bid_level_sizes is None:
            bid_level_sizes = [best_bid_size]
        if ask_level_sizes is None:
            ask_level_sizes = [best_ask_size]

        normal_book = best_ask_price > best_bid_price
        spread = (best_ask_price - best_bid_price) if normal_book else 0
        elig = (book_valid and self._prev_valid and normal_book
                and spread <= self.MAX_SPREAD_TICKS)
        self._prev_valid = book_valid

        if self._cooldown > 0:
            self._cooldown -= 1
            return None
        if not elig:
            return None

        w_bid = self._weighted(bid_level_sizes)
        w_ask = self._weighted(ask_level_sizes)

        buy_cond = (w_bid * 10 > self.BID_THRESH * w_ask) and (best_ask_price > 0)
        sell_cond = (w_ask * 10 > self.ASK_THRESH * w_bid) and (best_bid_price > 0)

        if buy_cond:                        # BUY checked first (RTL priority)
            self._cooldown = self.COOLDOWN
            return Decision(action=0, price=best_ask_price,         # lift the ask
                            size=self._lot(w_bid, w_ask, self.BID_THRESH))
        if sell_cond:
            self._cooldown = self.COOLDOWN
            return Decision(action=1, price=best_bid_price,         # hit the bid
                            size=self._lot(w_ask, w_bid, self.ASK_THRESH))
        return None

    def reset(self):
        """Clear the prev_valid gate and cooldown (e.g. after a feed gap)."""
        self._prev_valid = False
        self._cooldown = 0
