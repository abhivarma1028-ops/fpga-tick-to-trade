"""
Pre-trade Risk Guard — software implementation (mirrors the RTL risk_check.sv
that will sit in the FPGA data path in Milestone 2+).

Enforces per Rule 15c3-5 (SEC Market Access Rule):
  - Max single order size
  - Fat-finger price band (% away from reference)
  - Max gross position (long + short notional)
  - Max order rate (orders per second)
  - Hard kill-switch (trip once → block all orders)
"""

import time
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    max_order_shares:    int   = 1_000          # max shares per order
    max_position_shares: int   = 10_000         # max net position
    max_orders_per_sec:  int   = 10             # rate limit
    fat_finger_pct:      float = 5.0            # % from reference price
    reference_price:     int   = 0              # in ITCH fixed-point (/10000 = USD)


class RiskGuard:
    def __init__(self, config: RiskConfig | None = None):
        self.cfg        = config or RiskConfig()
        self._killed    = False
        self._position  = 0     # net shares (positive=long, negative=short)
        self._order_ts: list[float] = []

    # ------------------------------------------------------------------
    def kill(self, reason: str = "manual"):
        self._killed = True
        log.critical("KILL SWITCH TRIPPED: %s — all orders blocked", reason)

    def reset_kill(self):
        self._killed = False
        log.warning("Kill switch reset")

    # ------------------------------------------------------------------
    def check(self, action: int, price: int, size: int) -> tuple[bool, str]:
        """
        Returns (allowed: bool, reason: str).
        action: 0=BUY 1=SELL  price/size in ITCH fixed-point units.
        """
        if self._killed:
            return False, "kill_switch"

        if size > self.cfg.max_order_shares:
            return False, f"size {size} > max {self.cfg.max_order_shares}"

        if self.cfg.reference_price > 0:
            band = int(self.cfg.reference_price * self.cfg.fat_finger_pct / 100)
            if abs(price - self.cfg.reference_price) > band:
                return False, f"fat_finger: price {price} out of band"

        # Rate limit: count orders in last 1 second
        now = time.monotonic()
        self._order_ts = [t for t in self._order_ts if now - t < 1.0]
        if len(self._order_ts) >= self.cfg.max_orders_per_sec:
            return False, f"rate_limit: {len(self._order_ts)} orders/sec"

        # Position limit
        delta = size if action == 0 else -size
        if abs(self._position + delta) > self.cfg.max_position_shares:
            return False, f"position_limit: would reach {self._position + delta}"

        return True, "ok"

    def record_fill(self, action: int, shares: int):
        """Call after a confirmed fill to update position."""
        self._order_ts.append(time.monotonic())
        self._position += shares if action == 0 else -shares
