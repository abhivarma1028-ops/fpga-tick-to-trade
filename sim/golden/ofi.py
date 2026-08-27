"""
Golden model — bit-exact mirror of rtl/strategy_ofi.sv.

Order Flow Imbalance signal (Cont-Kukanov-Stoikov). Integer-only, matching the RTL:
per-update OFI increment (price-conditioned size deltas), a rolling window sum, and
a threshold decision. No division anywhere.
"""


def ofi_increment(prev, cur):
    """prev/cur = (bid_p, bid_s, ask_p, ask_s). Returns the signed OFI increment."""
    pbp, pbs, pap, pas = prev
    bp, bs, ap, asz = cur
    e_b = (bs if bp >= pbp else 0) - (pbs if bp <= pbp else 0)
    e_a = (asz if ap <= pap else 0) - (pas if ap >= pap else 0)
    return e_b - e_a


class OFIGolden:
    """Mirror of strategy_ofi.sv: WINDOW-tap shift register summed each valid tick."""
    def __init__(self, window=8, threshold=1500, base_lot=100, max_lot=300):
        self.window = window
        self.threshold = threshold
        self.base_lot = base_lot
        self.max_lot = max_lot
        self.reset()

    def reset(self):
        self._prev = None
        self._buf = [0] * self.window     # ring of recent increments

    def _lot(self, strength):
        # tiered by strength (compare-based, matches the RTL exactly)
        if strength >= 3 * self.threshold:
            sz = 3 * self.base_lot
        elif strength >= 2 * self.threshold:
            sz = 2 * self.base_lot
        else:
            sz = self.base_lot
        return min(self.max_lot, sz)

    def step(self, book_valid, bid_p, bid_s, ask_p, ask_s):
        """Returns (decision_valid, action, price, size). action 0=BUY 1=SELL."""
        cur = (bid_p, bid_s, ask_p, ask_s)
        normal = bool(book_valid) and bid_p > 0 and ask_p > 0 and ask_p > bid_p
        if not normal:
            self._prev = None
            return (0, 0, 0, 0)
        if self._prev is None:
            self._prev = cur
            return (0, 0, 0, 0)

        inc = ofi_increment(self._prev, cur)
        self._prev = cur
        self._buf = self._buf[1:] + [inc]           # shift in
        ofi = sum(self._buf)

        if ofi >= self.threshold:
            return (1, 0, ask_p, self._lot(ofi))
        if ofi <= -self.threshold:
            return (1, 1, bid_p, self._lot(-ofi))
        return (0, 0, 0, 0)
