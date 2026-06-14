"""
Live Market Data Feed Handler — IBKR software path.

Streams real top-of-book quotes from IB Gateway, runs each update through the
software mirror of the FPGA strategy (strategy_sw.SoftwareStrategy), applies the
pre-trade risk guard, and routes signals to the IBKR paper account via
ibkr_bridge.

This is the SOFTWARE comparison path. The FPGA computes the identical signal in
a hardware-measured 195 ns; here the same logic runs on live data so you can see
the strategy fire on a real book. End-to-end latency on this path is dominated by
network round-trip (~tens of ms) — that gap vs the FPGA's 195 ns is the point.

SAFETY: defaults to LIVE DATA + DRY-RUN ORDERS (orders are logged, not placed).
Pass --execute to actually place orders on your PAPER account. Never wired to a
live-money account.

Usage:
    # Offline: prove the software path with synthetic ticks (no Gateway needed)
    python live_feed.py --demo

    # Live data, dry-run orders (free delayed data)
    python live_feed.py --symbol AAPL

    # Live data, real-time subscription, actually place paper orders
    python live_feed.py --symbol AAPL --realtime --execute
"""

import math
import asyncio
import logging
import argparse

# basicConfig must precede any library import that might log, or it no-ops.
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

from strategy_sw import SoftwareStrategy
from ibkr_bridge import IBKRBridge, Decision as BridgeDecision, IB_AVAILABLE
from risk_guard import RiskConfig

log = logging.getLogger("live_feed")

# IBKR market-data type codes (ib.reqMarketDataType)
MKT_REALTIME       = 1
MKT_FROZEN         = 2
MKT_DELAYED        = 3   # free, ~15 min delayed
MKT_DELAYED_FROZEN = 4


def to_ticks(price_usd: float) -> int:
    """USD float -> ITCH fixed-point integer (price * 10,000)."""
    return int(round(price_usd * 10_000))


class LiveFeed:
    def __init__(self, symbol="AAPL", host="127.0.0.1", port=4002,
                 client_id=10, realtime=False, execute=False, threshold=15):
        self.symbol    = symbol
        self.host      = host
        self.port      = port
        self.client_id = client_id
        self.realtime  = realtime
        self.execute   = execute

        self.strategy = SoftwareStrategy(bid_thresh=threshold, ask_thresh=threshold)
        # Bridge runs in dry-run unless --execute; we inject our own connection
        # below so data and execution share one IB session.
        self.bridge = IBKRBridge(host=host, port=port, symbol=symbol,
                                 dry_run=not execute)
        self.bridge.risk.cfg = RiskConfig(
            max_order_shares    = 100,
            max_position_shares = 500,
            max_orders_per_sec  = 5,
            fat_finger_pct      = 2.0,
        )

        self.ib = None
        self._ref_set = False
        self.stats = {"ticks": 0, "signals": 0, "buy": 0, "sell": 0}

    # ------------------------------------------------------------------
    # Live IBKR path
    # ------------------------------------------------------------------
    async def run(self, duration=60):
        if not IB_AVAILABLE:
            log.error("ib_async not installed — run with --demo, or `pip install ib_async`")
            return

        from ib_async import IB, Stock
        self.ib = IB()
        log.info("Connecting to IB Gateway at %s:%d (clientId=%d) ...",
                 self.host, self.port, self.client_id)
        await self.ib.connectAsync(self.host, self.port, clientId=self.client_id)
        log.info("Connected. Market data: %s",
                 "REAL-TIME" if self.realtime else "DELAYED (free)")

        # Share this single connection with the bridge for order placement.
        self.bridge.ib = self.ib
        self.bridge._connected = True

        self.ib.reqMarketDataType(MKT_REALTIME if self.realtime else MKT_DELAYED)

        contract = Stock(self.symbol, 'SMART', 'USD')
        await self.ib.qualifyContractsAsync(contract)
        log.info("Subscribed to %s. %s orders. Running %ds ...",
                 self.symbol,
                 "EXECUTING paper" if self.execute else "DRY-RUN (logged, not placed)",
                 duration)

        ticker = self.ib.reqMktData(contract, '', False, False)
        self.ib.pendingTickersEvent += self._on_tickers

        try:
            await asyncio.sleep(duration)
        except (KeyboardInterrupt, asyncio.CancelledError):
            log.info("Interrupted — shutting down")
        finally:
            self.ib.pendingTickersEvent -= self._on_tickers
            self.ib.cancelMktData(contract)
            self.ib.disconnect()
            self._report()

    def _on_tickers(self, tickers):
        """Sync event handler. Schedules async order placement when a signal fires."""
        for t in tickers:
            dec = self._process(t.bid, t.bidSize, t.ask, t.askSize)
            if dec is not None:
                # Schedule the async send on the running loop.
                asyncio.ensure_future(self._send(dec))

    def _process(self, bid, bid_size, ask, ask_size):
        """Shared core: validate quote, run strategy. Returns BridgeDecision or None."""
        self.stats["ticks"] += 1

        def ok(x):
            return x is not None and not (isinstance(x, float) and math.isnan(x)) and x > 0

        book_valid = ok(bid) and ok(ask) and ok(bid_size) and ok(ask_size)

        bid_p = to_ticks(bid) if ok(bid) else 0
        ask_p = to_ticks(ask) if ok(ask) else 0
        bid_s = int(bid_size) if ok(bid_size) else 0
        ask_s = int(ask_size) if ok(ask_size) else 0

        # Seed the fat-finger reference price from the first valid mid.
        if book_valid and not self._ref_set:
            self.bridge.risk.cfg.reference_price = (bid_p + ask_p) // 2
            self._ref_set = True

        dec = self.strategy.evaluate(book_valid, bid_p, bid_s, ask_p, ask_s)
        if dec is None:
            return None

        self.stats["signals"] += 1
        self.stats["buy" if dec.action == 0 else "sell"] += 1
        side = "BUY" if dec.action == 0 else "SELL"
        log.info("SIGNAL %s  bid=%d@%.4f  ask=%d@%.4f  -> %s %d @ %.4f",
                 side, bid_s, bid_p / 1e4, ask_s, ask_p / 1e4,
                 side, dec.size, dec.price / 1e4)

        return BridgeDecision(action=dec.action, price_ticks=dec.price, size=dec.size)

    async def _send(self, bdec):
        await self.bridge.send_decision(bdec)

    def _report(self):
        s = self.stats
        log.info("==== SESSION SUMMARY ====")
        log.info("ticks processed : %d", s["ticks"])
        log.info("signals         : %d  (BUY=%d  SELL=%d)", s["signals"], s["buy"], s["sell"])
        log.info("net position    : %d shares", self.bridge.risk._position)

    # ------------------------------------------------------------------
    # Offline demo — no Gateway needed
    # ------------------------------------------------------------------
    async def demo(self):
        """Feed synthetic ticks through the full software path (dry-run)."""
        log.info("DEMO mode — synthetic ticks, no IBKR connection")
        # (bid_usd, bid_size, ask_usd, ask_size)
        ticks = [
            (150.00, 100, 150.05, 100),   # balanced — no signal
            (150.00, 100, 150.05, 100),   # balanced (warms prev_valid gate)
            (150.00, 500, 150.05,  80),   # bid-heavy 6.25x -> BUY
            (150.01, 100, 150.06, 100),   # balanced
            (150.01,  60, 150.06, 400),   # ask-heavy 6.7x -> SELL
            (150.02, 200, 150.07, 100),   # 2x bid -> BUY (>1.5x thresh)
        ]
        for (b, bs, a, asz) in ticks:
            dec = self._process(b, bs, a, asz)
            if dec is not None:
                await self.bridge.send_decision(dec)
            await asyncio.sleep(0.05)
        self._report()


def main():
    p = argparse.ArgumentParser(description="IBKR live market data -> strategy -> paper orders")
    p.add_argument('--symbol',    default='AAPL')
    p.add_argument('--host',      default='127.0.0.1')
    p.add_argument('--port',      type=int, default=4002, help='IB Gateway paper=4002, TWS paper=7497')
    p.add_argument('--client-id', type=int, default=10)
    p.add_argument('--realtime',  action='store_true', help='real-time data (needs subscription); default delayed/free')
    p.add_argument('--execute',   action='store_true', help='actually place PAPER orders (default: dry-run, logged only)')
    p.add_argument('--threshold', type=int, default=15, help='imbalance threshold *10 (15 = 1.5x)')
    p.add_argument('--duration',  type=int, default=60, help='seconds to run')
    p.add_argument('--demo',      action='store_true', help='offline synthetic-tick demo (no Gateway)')
    args = p.parse_args()

    feed = LiveFeed(symbol=args.symbol, host=args.host, port=args.port,
                    client_id=args.client_id, realtime=args.realtime,
                    execute=args.execute, threshold=args.threshold)

    if args.demo:
        asyncio.run(feed.demo())
    else:
        asyncio.run(feed.run(duration=args.duration))


if __name__ == '__main__':
    main()
