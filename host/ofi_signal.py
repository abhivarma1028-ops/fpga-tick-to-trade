"""
Order Flow Imbalance (OFI) signal — Cont, Kukanov & Stoikov (2014),
"The Price Impact of Order Book Events".

Their result: over short horizons, the mid-price change is driven, near-LINEARLY,
by the order-flow imbalance at the best bid/ask — not by static book levels. OFI
is the best-documented short-horizon predictor and (unlike our old depth-weighted
imbalance, which had negative IC on real AAPL) it measures order-flow EVENTS:

  Per L1 update n, compare to the previous top-of-book:
    bid event   e^b = q_b^n * 1{P_b^n >= P_b^{n-1}}  -  q_b^{n-1} * 1{P_b^n <= P_b^{n-1}}
    ask event   e^a = q_a^n * 1{P_a^n <= P_a^{n-1}}  -  q_a^{n-1} * 1{P_a^n >= P_a^{n-1}}
    OFI_n = e^b - e^a

  A positive OFI means net buying pressure (bid replenished / ask consumed) and
  predicts an up-move; negative predicts down. We accumulate OFI over a short
  rolling window and threshold it to a BUY/SELL decision.

Everything here is integer add / subtract / compare (NO division) -> maps directly
to RTL (see rtl/strategy_ofi.sv). Prices/sizes are ITCH fixed-point integers.
"""

from collections import deque
from dataclasses import dataclass


@dataclass
class Decision:
    action: int   # 0 = BUY, 1 = SELL
    price: int    # aggressive: ask for BUY, bid for SELL
    size: int


class OFISignal:
    def __init__(self, window: int = 10, threshold: int = 1500,
                 base_lot: int = 100, max_lot: int = 300):
        self.window = window
        self.threshold = threshold          # |windowed OFI| to trigger
        self.base_lot = base_lot
        self.max_lot = max_lot
        self._prev = None                   # (bid_p, bid_s, ask_p, ask_s)
        self._buf = deque(maxlen=window)     # recent per-tick OFI increments

    @staticmethod
    def ofi_increment(prev, cur) -> int:
        """Single-update OFI increment (CKS). prev/cur = (bid_p,bid_s,ask_p,ask_s)."""
        pbp, pbs, pap, pas = prev
        bp, bs, ap, asz = cur
        # bid side
        e_b = (bs if bp >= pbp else 0) - (pbs if bp <= pbp else 0)
        # ask side
        e_a = (asz if ap <= pap else 0) - (pas if ap >= pap else 0)
        return e_b - e_a

    def update(self, bid_p, bid_s, ask_p, ask_s, book_valid=True):
        """Feed one L1 quote; returns a Decision or None."""
        cur = (bid_p, bid_s, ask_p, ask_s)
        if not book_valid or bid_p <= 0 or ask_p <= 0 or ask_p <= bid_p:
            self._prev = cur if book_valid else None
            return None
        if self._prev is None:
            self._prev = cur
            return None

        self._buf.append(self.ofi_increment(self._prev, cur))
        self._prev = cur
        ofi = sum(self._buf)                # windowed OFI (linear aggregate)

        if ofi >= self.threshold:           # net buying pressure -> BUY
            return Decision(0, ask_p, self._lot(ofi))
        if ofi <= -self.threshold:          # net selling pressure -> SELL
            return Decision(1, bid_p, self._lot(-ofi))
        return None

    def _lot(self, strength: int) -> int:
        # tiered by OFI strength (compare-based; matches rtl/strategy_ofi.sv)
        if strength >= 3 * self.threshold:
            sz = 3 * self.base_lot
        elif strength >= 2 * self.threshold:
            sz = 2 * self.base_lot
        else:
            sz = self.base_lot
        return min(self.max_lot, sz)

    def reset(self):
        self._prev = None
        self._buf.clear()

    # raw windowed OFI value (for the alpha study)
    def ofi_value(self, bid_p, bid_s, ask_p, ask_s):
        cur = (bid_p, bid_s, ask_p, ask_s)
        if self._prev is None:
            self._prev = cur
            return 0
        self._buf.append(self.ofi_increment(self._prev, cur))
        self._prev = cur
        return sum(self._buf)
