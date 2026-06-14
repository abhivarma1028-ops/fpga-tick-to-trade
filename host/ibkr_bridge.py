"""
IBKR Paper Trading Bridge — Milestone 1
Receives decisions from the FPGA (or simulator) and routes them to
IB Gateway (paper account) via ib_async.

Usage:
    python ibkr_bridge.py          # connects to IB Gateway on localhost:4002
"""

import asyncio
import logging
import argparse
from dataclasses import dataclass

# Configure root logger before any other module can sneak in a StreamHandler,
# otherwise basicConfig becomes a no-op and INFO messages are silenced.
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

# ib_async: pip install ib_async
try:
    from ib_async import IB, MarketOrder, Stock, LimitOrder
    IB_AVAILABLE = True
except ImportError:
    IB_AVAILABLE = False
    logging.warning("ib_async not installed — bridge will run in DRY-RUN mode")

from risk_guard import RiskGuard, RiskConfig

log = logging.getLogger(__name__)


@dataclass
class Decision:
    action:      int    # 0=BUY 1=SELL
    price_ticks: int    # ITCH fixed-point (/10000 = USD)
    size:        int    # shares


class IBKRBridge:
    def __init__(self, host: str = '127.0.0.1', port: int = 4002,
                 symbol: str = 'AAPL', dry_run: bool = False):
        self.host    = host
        self.port    = port
        self.symbol  = symbol
        self.dry_run = dry_run or not IB_AVAILABLE
        self.ib      = IB() if IB_AVAILABLE else None
        self.risk    = RiskGuard(RiskConfig(
            max_order_shares    = 100,
            max_position_shares = 500,
            max_orders_per_sec  = 5,
            fat_finger_pct      = 2.0,
        ))
        self._connected = False

    async def connect(self):
        if self.dry_run:
            log.info("DRY-RUN mode — no real connection")
            return
        await self.ib.connectAsync(self.host, self.port, clientId=1)
        self._connected = True
        log.info("Connected to IB Gateway at %s:%d", self.host, self.port)

    async def disconnect(self):
        if self._connected and self.ib:
            self.ib.disconnect()

    async def send_decision(self, dec: Decision) -> bool:
        price_usd = dec.price_ticks / 10_000.0

        allowed, reason = self.risk.check(dec.action, dec.price_ticks, dec.size)
        if not allowed:
            log.warning("RISK BLOCK  action=%s size=%d price=%.4f  reason=%s",
                        'BUY' if dec.action == 0 else 'SELL',
                        dec.size, price_usd, reason)
            return False

        action_str = 'BUY' if dec.action == 0 else 'SELL'
        log.info("ORDER  %s %d @ $%.4f", action_str, dec.size, price_usd)

        self.risk.record_fill(dec.action, dec.size)

        if self.dry_run:
            log.info("DRY-RUN: would place %s %d @ %.4f", action_str, dec.size, price_usd)
            return True

        contract = Stock(self.symbol, 'SMART', 'USD')
        order    = LimitOrder(action_str, dec.size, price_usd)
        trade    = self.ib.placeOrder(contract, order)
        log.info("Placed order: %s", trade)
        return True

    # ------------------------------------------------------------------
    # Simulation feed — reads decisions from a queue (wired to cocotb or
    # replay_and_run.py in M1; wired to DMA in M2)
    # ------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# CLI entry point for manual testing
# ---------------------------------------------------------------------------
async def _demo():
    bridge = IBKRBridge(dry_run=True, symbol='AAPL')
    queue  = asyncio.Queue()

    # Feed some test decisions
    queue.put_nowait(Decision(action=0, price_ticks=1_500_000, size=100))  # BUY $150
    queue.put_nowait(Decision(action=1, price_ticks=1_500_500, size=100))  # SELL $150.05
    queue.put_nowait(None)  # sentinel

    await bridge.run_from_queue(queue)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true', help='Run dry-run demo')
    args = parser.parse_args()
    if args.demo:
        asyncio.run(_demo())
