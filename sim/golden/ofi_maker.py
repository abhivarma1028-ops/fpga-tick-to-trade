"""
Golden model — bit-exact mirror of rtl/strategy_ofi_maker.sv.

OFI-driven market maker: microprice (truncating divide) with the quote centre
tilted by the windowed OFI, then inventory skew + half-spread. Integer math only.
ALPHA_DEN is a power of two so the tilt is a shift in RTL (no divide).
"""


def ofi_increment(prev, cur):
    pbp, pbs, pap, pas = prev
    bp, bs, ap, asz = cur
    e_b = (bs if bp >= pbp else 0) - (pbs if bp <= pbp else 0)
    e_a = (asz if ap <= pap else 0) - (pas if ap >= pap else 0)
    return e_b - e_a


class OFIMakerGolden:
    def __init__(self, half_spread=200, skew=2, quote_size=100, max_position=1000,
                 ofi_window=8, alpha_shift=2, max_tilt=150):
        self.HS = half_spread
        self.SKEW = skew
        self.QS = quote_size
        self.MAXP = max_position
        self.WIN = ofi_window
        self.ASHIFT = alpha_shift          # tilt = ofi >> alpha_shift  (alpha_den = 2**shift)
        self.MAXT = max_tilt
        self.reset()

    def reset(self):
        self._prev = None
        self._buf = [0] * self.WIN

    def _tilt(self, ofi):
        # arithmetic shift right (floor division by 2**ASHIFT), then clamp
        t = ofi >> self.ASHIFT
        if t > self.MAXT:  t = self.MAXT
        if t < -self.MAXT: t = -self.MAXT
        return t

    def quote(self, book_valid, bid_p, bid_s, ask_p, ask_s, inventory):
        """Returns (quote_valid, bid_price, bid_qty, ask_price, ask_qty)."""
        cur = (bid_p, bid_s, ask_p, ask_s)
        den = bid_s + ask_s
        normal = bool(book_valid) and ask_p > bid_p and den != 0
        if not normal:
            self._prev = None
            return (0, 0, 0, 0, 0)

        if self._prev is None:
            self._prev = cur
            ofi = sum(self._buf)
        else:
            self._buf = self._buf[1:] + [ofi_increment(self._prev, cur)]
            self._prev = cur
            ofi = sum(self._buf)

        micro = (bid_p * ask_s + ask_p * bid_s) // den
        centre = micro - inventory * self.SKEW + self._tilt(ofi)
        bid = centre - self.HS
        ask = centre + self.HS
        if ask <= bid:
            ask = bid + 1

        bidq = askq = self.QS
        if inventory + self.QS > self.MAXP:  bidq = 0
        if inventory - self.QS < -self.MAXP: askq = 0
        bidp = 0 if (bid < 0 or bidq == 0) else bid
        askp = 0 if (ask < 0 or askq == 0) else ask
        return (1, bidp, bidq, askp, askq)
