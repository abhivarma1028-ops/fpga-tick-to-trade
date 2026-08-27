"""
OFI-driven market maker — the "predict + quote" hybrid.

A plain market maker quotes symmetrically around the microprice; it earns the
spread but is a sitting duck when the mid drifts (adverse selection). This maker
shifts its quote CENTRE by the OFI-predicted drift (Cont-Kukanov-Stoikov), so it
leans INTO the move it expects while still earning the spread:

    micro   = (bid_px*ask_sz + ask_px*bid_sz) / (bid_sz + ask_sz)     # fair value
    tilt    = clamp(ALPHA_NUM * OFI_window / ALPHA_DEN, ±MAX_TILT)     # predicted drift
    centre  = micro - inventory*SKEW + tilt
    bid     = centre - HALF_SPREAD ,  ask = centre + HALF_SPREAD

OFI_window is the same windowed order-flow-imbalance sum as strategy_ofi. Integer
math throughout (truncating divide for the microprice, like the MM RTL) so this
doubles as the golden model for rtl/strategy_ofi_maker.sv. Positive OFI -> centre
up -> keener to buy ahead of an up-move; the tilt is capped below HALF_SPREAD so
quotes never invert.
"""

from dataclasses import dataclass


@dataclass
class OFIMakerConfig:
    half_spread: int = 200
    skew: int = 2
    quote_size: int = 100
    max_position: int = 1000
    ofi_window: int = 8
    alpha_num: int = 1
    alpha_den: int = 4        # tilt = alpha_num*ofi // alpha_den
    max_tilt: int = 150       # < half_spread so quotes never cross


def ofi_increment(prev, cur):
    """CKS single-update OFI increment. prev/cur = (bid_p,bid_s,ask_p,ask_s)."""
    pbp, pbs, pap, pas = prev
    bp, bs, ap, asz = cur
    e_b = (bs if bp >= pbp else 0) - (pbs if bp <= pbp else 0)
    e_a = (asz if ap <= pap else 0) - (pas if ap >= pap else 0)
    return e_b - e_a


@dataclass
class Quote:
    bid_price: int
    bid_size: int
    ask_price: int
    ask_size: int


class OFIMaker:
    def __init__(self, cfg: OFIMakerConfig = None):
        self.cfg = cfg or OFIMakerConfig()
        self._prev = None
        self._buf = [0] * self.cfg.ofi_window

    def reset(self):
        self._prev = None
        self._buf = [0] * self.cfg.ofi_window

    def _tilt(self, ofi):
        c = self.cfg
        t = (c.alpha_num * ofi) // c.alpha_den
        if t > c.max_tilt:  t = c.max_tilt
        if t < -c.max_tilt: t = -c.max_tilt
        return t

    def quote(self, bid_p, bid_s, ask_p, ask_s, inventory, book_valid=True):
        c = self.cfg
        cur = (bid_p, bid_s, ask_p, ask_s)
        den = bid_s + ask_s
        normal = bool(book_valid) and ask_p > bid_p and den != 0
        if not normal:
            self._prev = None
            return Quote(0, 0, 0, 0)

        # advance the windowed OFI (needs a previous quote)
        if self._prev is None:
            self._prev = cur
            ofi = sum(self._buf)             # unchanged (all zeros initially)
        else:
            self._buf = self._buf[1:] + [ofi_increment(self._prev, cur)]
            self._prev = cur
            ofi = sum(self._buf)

        micro = (bid_p * ask_s + ask_p * bid_s) // den
        centre = micro - inventory * c.skew + self._tilt(ofi)
        bid = centre - c.half_spread
        ask = centre + c.half_spread
        if ask <= bid:
            ask = bid + 1

        bidq = aq = c.quote_size
        if inventory + c.quote_size > c.max_position:  bidq = 0
        if inventory - c.quote_size < -c.max_position: aq = 0
        bidp = 0 if (bid < 0 or bidq == 0) else bid
        askp = 0 if (ask < 0 or aq == 0) else ask
        return Quote(bidp, bidq, askp, aq)
