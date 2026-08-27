"""
alpaca_bridge — Alpaca broker adapter for crypto (BTC/USD and friends).

Same contract as ibkr_bridge.IBKRBridge, so live_feed / monitor_server can use
either: connect() / disconnect() / send_decision(Decision) -> bool /
run_from_queue(queue).

WHY A SEPARATE RISK CONFIG FROM THE EQUITY PATH
-----------------------------------------------
The RTL and the software strategies speak equities: `Decision.size` is an
integer share count (LOT_SIZE=100) and `price_ticks/10_000` is USD. Those units
are meaningless for crypto -- 100 "shares" of BTC is roughly $6,000,000, and the
equity RiskGuard caps (max_order_shares=100, max_position_shares=500) would wave
that straight through.

So this bridge converts lots to a base-asset quantity and enforces every limit in
USD NOTIONAL, which is the only unit that means the same thing across assets.

SAFETY DEFAULTS
---------------
`dry_run=True` and `paper=True`. Nothing reaches a real venue unless BOTH are
turned off deliberately, and even on the live account every order is still capped
by max_notional_usd. Credentials come from the environment, never from source:

    export APCA_API_KEY_ID=...
    export APCA_API_SECRET_KEY=...

Create the key with trading enabled, withdrawals DISABLED, and an IP allowlist.
"""
from __future__ import annotations

import os
import time
import asyncio
import logging
from dataclasses import dataclass, field
from collections import deque

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import LimitOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    ALPACA_AVAILABLE = True
except ImportError:                                  # pragma: no cover
    ALPACA_AVAILABLE = False

try:
    from ibkr_bridge import Decision                 # reuse the same shape
except ImportError:                                  # standalone use
    @dataclass
    class Decision:
        action: int          # 0=BUY 1=SELL
        price_ticks: int     # /10_000 = USD
        size: int            # lots, in RTL "share" units

log = logging.getLogger(__name__)

TICKS_PER_USD = 10_000.0
RTL_LOT       = 100          # the RTL's LOT_SIZE, in "shares"


@dataclass
class CryptoRiskConfig:
    """Every limit in USD notional -- the one unit that travels across assets."""
    symbol:             str   = "BTC/USD"
    qty_per_lot:        float = 0.0001   # base-asset qty for one RTL lot (100 "shares")
    max_notional_usd:   float = 10_000.0 # per order
    max_position_usd:   float = 10_000.0 # absolute exposure
    max_orders_per_sec: int   = 2
    fat_finger_pct:     float = 2.0      # reject a price this far from the last seen
    min_qty:            float = 0.0001   # Alpaca BTC/USD step


class AlpacaBridge:
    def __init__(self, cfg: CryptoRiskConfig | None = None,
                 paper: bool = True, dry_run: bool = True):
        self.cfg     = cfg or CryptoRiskConfig()
        self.paper   = paper
        # dry_run also forced on if the SDK or credentials are absent, so an
        # incomplete setup can never silently become a live order path.
        self.dry_run = dry_run or not ALPACA_AVAILABLE
        self.client  = None

        self._key    = os.environ.get("APCA_API_KEY_ID")
        self._secret = os.environ.get("APCA_API_SECRET_KEY")

        self._position_qty = 0.0     # signed, base asset
        self._last_price   = None
        self._order_times  = {}      # rate_key -> deque of send times
        self._sent         = 0
        self._blocked      = 0

    # ------------------------------------------------------------------ conn
    async def connect(self):
        if self.dry_run:
            why = ("alpaca-py not installed" if not ALPACA_AVAILABLE else "dry_run=True")
            log.info("DRY-RUN (%s) — no orders will be transmitted", why)
            return
        if not (self._key and self._secret):
            self.dry_run = True
            log.error("APCA_API_KEY_ID / APCA_API_SECRET_KEY not set — forcing DRY-RUN")
            return
        self.client = TradingClient(self._key, self._secret, paper=self.paper)
        acct = self.client.get_account()
        log.info("Connected to Alpaca %s account: equity=%s buying_power=%s",
                 "PAPER" if self.paper else "*** LIVE ***",
                 acct.equity, acct.buying_power)

    async def disconnect(self):
        log.info("Alpaca bridge closing: %d sent, %d risk-blocked", self._sent, self._blocked)
        self.client = None

    # ------------------------------------------------------------------ risk
    def _check(self, side: str, price_usd: float, qty: float,
               rate_key: str = 'default') -> tuple[bool, str]:
        notional = qty * price_usd

        if qty < self.cfg.min_qty:
            return False, f"qty {qty:.8f} below step {self.cfg.min_qty}"
        if notional > self.cfg.max_notional_usd:
            return False, (f"order notional ${notional:,.2f} > cap "
                           f"${self.cfg.max_notional_usd:,.2f}")

        signed = qty if side == "BUY" else -qty
        proj   = abs((self._position_qty + signed) * price_usd)
        if proj > self.cfg.max_position_usd:
            return False, (f"projected exposure ${proj:,.2f} > cap "
                           f"${self.cfg.max_position_usd:,.2f}")

        if self._last_price is not None and self._last_price > 0:
            drift = abs(price_usd - self._last_price) / self._last_price * 100.0
            if drift > self.cfg.fat_finger_pct:
                return False, (f"price ${price_usd:,.2f} is {drift:.1f}% from last "
                               f"${self._last_price:,.2f} (fat-finger)")

        # Rate budget is PER CALLER, not global. With one shared budget the
        # first strategy in iteration order consumes it every tick and every
        # other strategy is starved -- ofi produced 2101 decisions and got zero
        # orders through before this was split.
        now = time.time()
        dq = self._order_times.setdefault(rate_key, deque(maxlen=64))
        while dq and now - dq[0] > 1.0:
            dq.popleft()
        if len(dq) >= self.cfg.max_orders_per_sec:
            return False, f"rate limit {self.cfg.max_orders_per_sec}/s for {rate_key}"

        return True, ""

    # ------------------------------------------------------------------ send
    async def send_decision(self, dec: Decision) -> bool:
        price_usd = dec.price_ticks / TICKS_PER_USD
        qty       = (dec.size / RTL_LOT) * self.cfg.qty_per_lot
        side      = "BUY" if dec.action == 0 else "SELL"

        ok, reason = self._check(side, price_usd, qty, "async")
        if not ok:
            self._blocked += 1
            # NOTE: %-style logging has no thousands flag ("%,.2f" raises), so
            # anything with separators is formatted before it is handed over.
            log.warning("RISK BLOCK  %s %.8f %s @ $%s — %s",
                        side, qty, self.cfg.symbol, f"{price_usd:,.2f}", reason)
            return False

        self._order_times.setdefault("async", deque(maxlen=64)).append(time.time())
        self._last_price = price_usd
        self._position_qty += qty if side == "BUY" else -qty
        self._sent += 1

        notional = qty * price_usd
        log.info("ORDER  %s %.8f %s @ $%s  (notional $%s)",
                 side, qty, self.cfg.symbol, f"{price_usd:,.2f}", f"{notional:,.2f}")

        if self.dry_run:
            log.info("DRY-RUN: not transmitted")
            return True

        req = LimitOrderRequest(
            symbol        = self.cfg.symbol,
            qty           = round(qty, 8),
            side          = OrderSide.BUY if dec.action == 0 else OrderSide.SELL,
            time_in_force = TimeInForce.GTC,      # crypto: GTC/IOC only, not DAY
            limit_price   = round(price_usd, 2),
        )
        try:
            order = self.client.submit_order(req)
            log.info("Submitted %s id=%s status=%s", self.cfg.symbol, order.id, order.status)
            return True
        except Exception as e:
            log.error("Order rejected by Alpaca: %s", e)
            self._position_qty -= qty if side == "BUY" else -qty   # roll back
            self._sent -= 1
            return False

    def send_decision_sync(self, dec: Decision, rate_key: str = "default") -> tuple[bool, str]:
        """Synchronous entry point for thread-based callers (monitor_server runs
        its feed in a plain thread, not an event loop).

        Returns (accepted, reason). The risk checks are pure computation; only
        the actual submit does I/O, and that is skipped entirely in dry-run.
        """
        price_usd = dec.price_ticks / TICKS_PER_USD
        qty       = (dec.size / RTL_LOT) * self.cfg.qty_per_lot
        side      = "BUY" if dec.action == 0 else "SELL"

        ok, reason = self._check(side, price_usd, qty, rate_key)
        if not ok:
            self._blocked += 1
            return False, reason

        self._order_times.setdefault(rate_key, deque(maxlen=64)).append(time.time())
        self._last_price = price_usd
        self._position_qty += qty if side == "BUY" else -qty
        self._sent += 1

        if self.dry_run:
            return True, "dry-run"

        req = LimitOrderRequest(
            symbol        = self.cfg.symbol,
            qty           = round(qty, 8),
            side          = OrderSide.BUY if dec.action == 0 else OrderSide.SELL,
            time_in_force = TimeInForce.GTC,
            limit_price   = round(price_usd, 2),
        )
        try:
            order = self.client.submit_order(req)
            return True, f"submitted {order.id}"
        except Exception as e:
            self._position_qty -= qty if side == "BUY" else -qty
            self._sent -= 1
            log.error("Order rejected by Alpaca: %s", e)
            return False, f"rejected: {e}"

    def arm(self, paper: bool = True, live: bool = False):
        """Move between dry-run / paper / live. Returns the resulting mode."""
        self.paper = paper
        self.dry_run = (not live) or (not ALPACA_AVAILABLE)
        if not self.dry_run and not (self._key and self._secret):
            self.dry_run = True
            log.error("cannot arm: APCA_API_KEY_ID / APCA_API_SECRET_KEY not set")
        if not self.dry_run and self.client is None:
            self.client = TradingClient(self._key, self._secret, paper=self.paper)
        return self.stats()["mode"]

    # ------------------------------------------------------------------ feed
    async def run_from_queue(self, queue: asyncio.Queue):
        await self.connect()
        try:
            while True:
                dec = await queue.get()
                if dec is None:
                    break
                await self.send_decision(dec)
        finally:
            await self.disconnect()

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        return {
            "broker":       "alpaca",
            "mode":         "dry-run" if self.dry_run else ("paper" if self.paper else "LIVE"),
            "symbol":       self.cfg.symbol,
            "sent":         self._sent,
            "blocked":      self._blocked,
            "position_qty": round(self._position_qty, 8),
            "position_usd": round(self._position_qty * (self._last_price or 0), 2),
            "last_price":   self._last_price,
        }


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Alpaca crypto bridge smoke test")
    ap.add_argument("--symbol", default="BTC/USD")
    ap.add_argument("--live", action="store_true",
                    help="actually transmit (still paper unless --real is given)")
    ap.add_argument("--real", action="store_true",
                    help="use the LIVE account instead of paper — real money")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    async def main():
        br = AlpacaBridge(CryptoRiskConfig(symbol=args.symbol),
                          paper=not args.real, dry_run=not args.live)
        await br.connect()
        # one allowed order, then three that must be blocked
        await br.send_decision(Decision(action=0, price_ticks=int(60_000 * TICKS_PER_USD), size=100))
        await br.send_decision(Decision(action=0, price_ticks=int(60_000 * TICKS_PER_USD), size=100_000))
        await br.send_decision(Decision(action=0, price_ticks=int(90_000 * TICKS_PER_USD), size=100))
        await br.send_decision(Decision(action=0, price_ticks=int(60_000 * TICKS_PER_USD), size=1))
        await br.disconnect()
        print("\nstats:", br.stats())

    asyncio.run(main())
