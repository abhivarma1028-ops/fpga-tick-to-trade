"""
Avellaneda-Stoikov optimal market maker.

The classic 2008 model ("High-frequency trading in a limit order book"). Where our
heuristic MarketMaker uses a fixed half-spread + linear inventory skew, A-S derives
BOTH the quote centre and the spread from inventory, volatility, risk aversion, and
time-to-horizon:

  reservation price   r(q) = s - q * gamma * sigma^2 * (T - t)
      (skew the centre AWAY from inventory q; more when volatile / risk-averse)
  optimal spread      delta = gamma * sigma^2 * (T - t) + (2/gamma) * ln(1 + gamma/kappa)
  quotes              bid = r - delta/2 ,  ask = r + delta/2

  s      = mid (or microprice) fair value
  q      = current inventory (signed)
  gamma  = risk aversion (higher -> tighter inventory control, wider/■skewed quotes)
  sigma  = short-term volatility (per step)
  kappa  = order-flow liquidity (higher -> tighter spread; fills arrive easily)
  T - t  = time left in the session (normalised 1 -> 0); shrinks skew near close

This module is quoting logic only (integer ticks in/out, like market_maker.py) so it
drops into host/backtest.py. sigma is estimated live from a rolling mid window.
"""

import math
from collections import deque
from dataclasses import dataclass


@dataclass
class ASConfig:
    # NOTE on scale: the A-S inventory term is q*gamma*sigma^2*(T-t). sigma here is
    # in TICKS/step, so sigma^2 is large; gamma must be tiny to keep the skew at a
    # few ticks/share. Defaults below give ~2-4 ticks/share skew on the synthetic
    # feed. skew and half-spread are also hard-capped so a vol spike can't fling
    # quotes off the book (the bug an uncalibrated A-S hits).
    gamma: float = 1.0e-4       # risk aversion (scaled for tick-unit sigma)
    kappa: float = 1.5          # order-book liquidity
    vol_window: int = 30        # ticks for the rolling sigma estimate
    quote_size: int = 100
    max_position: int = 1000
    min_half_ticks: int = 200   # floor on half-spread (2c). On noisy feeds a too-tight
                                # A-S quote eats adverse selection; 200 matches the
                                # heuristic maker so the comparison isolates the quoting logic.
    max_half_ticks: int = 800   # cap half-spread ($0.08) — bound pathological spreads
    max_skew_ticks: int = 600   # cap |reservation - fair| ($0.06) — bound inventory skew
    horizon: int = 2000         # steps in a "session" (drives T-t)


@dataclass
class Quote:
    bid_price: int
    bid_size: int
    ask_price: int
    ask_size: int


class AvellanedaStoikov:
    def __init__(self, cfg: ASConfig = None):
        self.cfg = cfg or ASConfig()
        self._mids = deque(maxlen=self.cfg.vol_window)
        self._step = 0

    def _sigma_ticks(self) -> float:
        """Rolling stdev of mid *changes* (in ticks) = short-term volatility."""
        m = self._mids
        if len(m) < 3:
            return 1.0
        d = [m[i] - m[i - 1] for i in range(1, len(m))]
        mean = sum(d) / len(d)
        var = sum((x - mean) ** 2 for x in d) / len(d)
        return math.sqrt(var) or 1.0

    def quote(self, bid_p, bid_s, ask_p, ask_s, inventory: int) -> Quote:
        cfg = self.cfg
        if bid_p <= 0 or ask_p <= 0 or ask_p <= bid_p:
            return Quote(0, 0, 0, 0)
        # size-weighted fair value (microprice), fall back to mid
        denom = bid_s + ask_s
        fair = ((bid_p * ask_s + ask_p * bid_s) / denom) if denom else (bid_p + ask_p) / 2

        self._mids.append(fair)
        self._step += 1
        sigma = self._sigma_ticks()
        t_left = max(0.05, 1.0 - (self._step % cfg.horizon) / cfg.horizon)   # 1 -> ~0

        # A-S reservation price + optimal spread (in ticks), with the skew and
        # half-spread bounded so an uncalibrated vol estimate can't send quotes
        # off the book (the failure mode of raw A-S on a noisy tick feed).
        q = inventory
        skew = q * cfg.gamma * (sigma ** 2) * t_left
        skew = max(-cfg.max_skew_ticks, min(cfg.max_skew_ticks, skew))
        reservation = fair - skew
        delta = cfg.gamma * (sigma ** 2) * t_left + (2.0 / cfg.gamma) * math.log(1 + cfg.gamma / cfg.kappa)
        half = min(cfg.max_half_ticks, max(cfg.min_half_ticks, delta / 2.0))

        bid = int(round(reservation - half))
        ask = int(round(reservation + half))
        if ask <= bid:
            ask = bid + 1

        q_out = Quote(bid, cfg.quote_size, ask, cfg.quote_size)
        # position guard (same as the heuristic maker)
        if inventory + cfg.quote_size > cfg.max_position:
            q_out.bid_price, q_out.bid_size = 0, 0
        if inventory - cfg.quote_size < -cfg.max_position:
            q_out.ask_price, q_out.ask_size = 0, 0
        return q_out
