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

    # Trade ALL Magnificent-7 ($1T+) names concurrently (dry-run)
    python live_feed.py --universe mag7

    # Arbitrary basket
    python live_feed.py --symbols AAPL,MSFT,NVDA --simulate

    # Market-making mode (post two-sided quotes, earn the spread) — sim only
    python live_feed.py --universe mag7 --simulate --maker
"""

import os
import math
import time
import random
import asyncio
import logging
import argparse

# basicConfig must precede any library import that might log, or it no-ops.
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

from strategy_sw import SoftwareStrategy
from ibkr_bridge import IBKRBridge, Decision as BridgeDecision, IB_AVAILABLE
from risk_guard import RiskConfig
from portfolio import Portfolio
from hft_logic import PreTradeGauntlet, HFTConfig
from market_maker import MarketMaker, MMConfig

log = logging.getLogger("live_feed")

# IBKR market-data type codes (ib.reqMarketDataType)
MKT_REALTIME       = 1
MKT_FROZEN         = 2
MKT_DELAYED        = 3   # free, ~15 min delayed
MKT_DELAYED_FROZEN = 4

# Tick-to-trade latency of the FPGA path, measured in simulation through the
# latency_counter (sim/tb_phase3_pipeline.py): 41 cycles @ 200 MHz.
FPGA_LATENCY_NS = 205

# Named symbol universes. "mag7" = the Magnificent 7 mega-caps (all ~$1T+ market
# cap). Each symbol gets its own LiveFeed (own book/strategy/position/IB session);
# they run concurrently. Extend or add universes here as needed.
UNIVERSES = {
    "mag7": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"],
}


def to_ticks(price_usd: float) -> int:
    """USD float -> ITCH fixed-point integer (price * 10,000)."""
    return int(round(price_usd * 10_000))


class LiveFeed:
    def __init__(self, symbol="AAPL", host="127.0.0.1", port=4002,
                 client_id=10, realtime=False, execute=False, threshold=15,
                 portfolio=None, gauntlet=None, maker=False, control=None,
                 stop_loss=0.0, take_profit=0.0, cooldown_s=0.0,
                 half_spread_cents=3.0, quote_size=100, requote_s=3.0, skew=2.0,
                 max_inventory=300, max_hold_s=45.0, good_spread_cents=5.0,
                 momentum=False, order_size=1200, size_boost=2.0, tight_spread_cents=3.0):
        self.symbol    = symbol
        self.host      = host
        self.port      = port
        self.client_id = client_id
        self.realtime  = realtime
        self.execute   = execute
        self.maker     = maker           # market-making mode (simulate only)
        self.control   = control         # shared LiveControl (pause/flatten); may be None
        # per-position risk management (0 = off)
        self.stop_loss    = stop_loss    # flatten symbol if unrealized <= -stop_loss ($)
        self.take_profit  = take_profit  # flatten symbol if unrealized >= +take_profit ($)
        self.cooldown_s   = cooldown_s   # min seconds between entries (anti-churn)
        self._last_trade_t = 0.0
        self._risk_closing = False       # guard: a stop/target close is in flight

        self.strategy = SoftwareStrategy(bid_thresh=threshold, ask_thresh=threshold)
        # live market-maker config: quote a real cents-wide spread, skew HARD on
        # inventory so we revert to flat fast (a MM earns spread, doesn't carry risk)
        self.mm       = MarketMaker(MMConfig(
            half_spread_ticks=int(half_spread_cents * 100),   # cents -> ticks (1 tick=$0.0001)
            quote_size=quote_size, skew_ticks_per_share=skew, max_position=max_inventory))
        self.requote_s = requote_s        # how often to re-post two-sided quotes
        self.good_spread_ticks = int(good_spread_cents * 100)  # spread >= this = "good" -> capture now
        self._last_capture = 0.0
        self._flat_toggle = False          # when flat, alternate bid/ask (Alpaca 1-side rule)
        self.max_hold_s = max_hold_s      # flatten a position held longer than this (0=off)
        self._pos_since = None            # timestamp the current position opened
        self._last_market = None          # latest (bid_p, bid_s, ask_p, ask_s) in ticks
        # momentum-taker mode: buy big on an up-move (directional, aggressive)
        self.momentum  = momentum
        self.order_size = order_size
        self.size_boost = size_boost           # x order_size when the spread is tight (good fill)
        self.tight_ticks = int(tight_spread_cents * 100)
        self._prev_mid = None

        # HFT decision overlay. A shared Portfolio + a per-symbol gauntlet are
        # passed in for multi-symbol runs; a single-symbol run gets its own so
        # the object is never None.
        self.portfolio = portfolio if portfolio is not None else Portfolio()
        self.gauntlet  = gauntlet if gauntlet is not None else \
            PreTradeGauntlet(self.portfolio, HFTConfig())
        # Bridge runs in dry-run unless --execute; we inject our own connection
        # below so data and execution share one IB session.
        self.bridge = IBKRBridge(host=host, port=port, symbol=symbol,
                                 dry_run=not execute)
        # Mirror the FPGA risk_check.sv params so the software path is
        # FPGA-equivalent: MAX_ORDER_SIZE=500, MAX_POSITION=1000,
        # MAX_PRICE_BAND=5000 ticks ($0.50 ≈ 0.33% at ~$150). The RTL has no rate
        # limit, so set it high (the rate cap is a software-only extra guard).
        # momentum mode wants LARGE (>1000) positions, so raise the caps; otherwise
        # keep the FPGA-equivalent risk_check.sv params (500 / 1000).
        self.bridge.risk.cfg = RiskConfig(
            max_order_shares    = max(int(order_size * size_boost) + 1, 500) if momentum else 500,
            max_position_shares = max(order_size * 10, 1000) if momentum else 1000,
            max_orders_per_sec  = 100_000,
            fat_finger_pct      = 0.33,
        )

        self.ib = None
        self._trading_client = None
        self._open_orders = {}    # id -> action(0/1), for cancel-on-new
        self._pending = {}        # order id -> {lat, req} for attributing fill latency + slippage
        self._book_on_submit = True  # False on a real-broker execute path (book from real fills)
        self._pending_lat = None     # latency of the last IBKR order, for its fill
        self._pending_req = None     # requested price (ticks) of the last IBKR order, for slippage
        from collections import Counter as _Counter
        self.order_events = _Counter()  # fill / partial_fill / rejected / canceled counts
        self._ref_set = False
        self.stats = {"ticks": 0, "signals": 0, "buy": 0, "sell": 0,
                      "lat_ns_sum": 0, "lat_ns_min": None, "lat_ns_max": 0}
        self.lat_samples = []     # per-signal sw compute latency (ns), for charting
        self.net_ms_samples = []  # real order submit->ack round-trip (ms), execute path

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

        # EXECUTE: drive the portfolio from REAL executions, not assumed fills.
        if self.execute:
            self._book_on_submit = False
            self.ib.execDetailsEvent += self._on_exec

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

        # Measure the software strategy compute latency (tick -> signal). This is
        # the apples-to-apples counterpart of the FPGA's 205 ns tick-to-trade —
        # before any network round-trip on the order side.
        t0 = time.perf_counter_ns()
        dec = self.strategy.evaluate(book_valid, bid_p, bid_s, ask_p, ask_s)
        lat_ns = time.perf_counter_ns() - t0

        # Keep the HFT overlay's market view current on EVERY valid quote (even
        # ones with no signal), so volatility / staleness / exposure stay live.
        if book_valid:
            mid = (bid_p + ask_p) // 2
            self.gauntlet.observe(self.symbol, mid)
            self.portfolio.mark_price(self.symbol, mid)
            self.portfolio.set_quote(self.symbol, bid_p, ask_p)

        if dec is None:
            return None

        self.stats["signals"] += 1
        self.stats["buy" if dec.action == 0 else "sell"] += 1
        self.stats["lat_ns_sum"] += lat_ns
        self.stats["lat_ns_max"] = max(self.stats["lat_ns_max"], lat_ns)
        self.stats["lat_ns_min"] = (lat_ns if self.stats["lat_ns_min"] is None
                                    else min(self.stats["lat_ns_min"], lat_ns))
        self.lat_samples.append(lat_ns)
        side = "BUY" if dec.action == 0 else "SELL"
        log.info("SIGNAL %s [%s]  bid=%d@%.4f  ask=%d@%.4f  -> %s %d @ %.4f  "
                 "[sw compute %d ns | FPGA-equiv %d ns]",
                 side, self.symbol, bid_s, bid_p / 1e4, ask_s, ask_p / 1e4,
                 side, dec.size, dec.price / 1e4, lat_ns, FPGA_LATENCY_NS)

        # --- pre-trade gauntlet: the HFT decision brain (may veto or resize) ---
        permitted, reason = self.gauntlet.evaluate(
            self.symbol, dec, bid_p, bid_s, ask_p, ask_s,
            position=self.bridge.risk._position)
        if permitted is None:
            log.info("HFT VETO [%s]  %s %d @ %.4f  blocked by %s",
                     self.symbol, side, dec.size, dec.price / 1e4, reason)
            return None
        if permitted.size != dec.size:
            log.info("HFT RESIZE [%s]  %s %d -> %d shares (gross-exposure cap)",
                     self.symbol, side, dec.size, permitted.size)

        bd = BridgeDecision(action=permitted.action,
                            price_ticks=permitted.price, size=permitted.size)
        bd.lat_ns = lat_ns          # per-trade compute time for the live monitor
        return bd

    async def _send(self, bdec):
        if self.control and self.control.paused:
            return                              # trading paused from the page
        if self.cooldown_s and (time.time() - self._last_trade_t) < self.cooldown_s:
            return                              # anti-churn cooldown
        self._last_trade_t = time.time()
        placed = await self.bridge.send_decision(bdec)
        # Book here only for sim/demo/dry-run (no real broker). On a real IBKR
        # execute path _book_on_submit is False and the portfolio is driven by the
        # real execDetails fill event instead.
        if placed and self._book_on_submit:
            self.portfolio.on_fill(self.symbol, bdec.action, bdec.size,
                                   bdec.price_ticks, lat_ns=getattr(bdec, "lat_ns", None))
        elif placed:
            self._pending_lat = getattr(bdec, "lat_ns", None)  # attribute next IBKR fill
            self._pending_req = bdec.price_ticks                # for its slippage

    async def _alpaca_submit(self, bdec):
        """Route an Alpaca-path decision through pre-trade risk + an
        order-management layer, then either log (dry-run) or place a paper limit
        order on Alpaca.

        Order management (fixes the two real-broker issues seen in Phase 4):
          * Inventory clamp (#7): clamp size to the net-position capacity left in
            this direction, so we trade UP TO the cap and stop — instead of
            overshooting or having the whole order rejected. Reducing trades
            (toward flat) are never clamped.
          * Cancel-on-new (#6): cancel any of our still-working orders on the
            OPPOSITE side before submitting, so a new aggressive limit can't
            cross our own resting order (Alpaca wash-trade reject 40310000).
        """
        if self.control and self.control.paused:
            return                              # trading paused from the page
        if self.cooldown_s and (time.time() - self._last_trade_t) < self.cooldown_s:
            return                              # anti-churn cooldown
        self._last_trade_t = time.time()
        action_str = 'BUY' if bdec.action == 0 else 'SELL'
        price_usd   = bdec.price_ticks / 10_000.0

        # Reconcile our position estimate with the broker's ACTUAL position so the
        # clamp below can't drift from reality (fixes #7 residual). No-op in dry-run.
        await self._sync_position()

        # --- inventory clamp (before the risk check, so it never trips position) ---
        # Two rules: (1) never CROSS zero in a single order — a broker rejects an
        # order that flips long<->short in one shot, so sell at most the long held
        # (to flat) / buy at most the short held (to cover); (2) extending in the
        # same direction is capped at max_position. This also keeps SELL size <=
        # held inventory, so IOC partial fills can't leave us requesting > available.
        pos = self.bridge.risk._position
        cap = self.bridge.risk.cfg.max_position_shares
        if bdec.action == 0:                      # BUY
            room = (-pos) if pos < 0 else (cap - pos)   # cover short, else extend long
        else:                                     # SELL
            room = pos if pos > 0 else (cap + pos)      # sell long, else extend short
        size = max(0, min(bdec.size, room))
        if size == 0:
            log.info("POSITION CAP  %s skipped (pos=%d, cap=%d) — no capacity",
                     action_str, pos, cap)
            return
        if size != bdec.size:
            log.info("CLAMP  %s %d -> %d shares (pos=%d, cap=%d)",
                     action_str, bdec.size, size, pos, cap)
        bdec.size = size

        # --- remaining pre-trade risk (fat-finger, max order size, rate) ---
        allowed, reason = self.bridge.risk.check(bdec.action, bdec.price_ticks, bdec.size)
        if not allowed:
            log.warning("RISK BLOCK  %s size=%d price=%.4f  reason=%s",
                        action_str, bdec.size, price_usd, reason)
            return
        self.bridge.risk.record_fill(bdec.action, bdec.size)
        log.info("ORDER  %s %d @ $%.4f", action_str, bdec.size, price_usd)

        if self._trading_client is None:   # dry-run: no broker, so book the assumed fill now
            self.portfolio.on_fill(self.symbol, bdec.action, bdec.size, bdec.price_ticks,
                                   lat_ns=getattr(bdec, "lat_ns", None))
            log.info("DRY-RUN: would place %s %d @ %.4f", action_str, bdec.size, price_usd)
            return
        # EXECUTE: do NOT book here — the portfolio is driven by the REAL fill event
        # (_on_trade_update) with the broker's actual qty / price / fee.

        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        # cancel-on-new: clear our working orders on the opposite side first.
        await self._cancel_opposite(bdec.action)

        # IOC (immediate-or-cancel): the order fills now or is cancelled — it never
        # rests. We pad the limit a few cents THROUGH the touch so the IOC is
        # actually marketable (the IEX-derived quote lags the fill-side NBBO, so an
        # exactly-at-touch limit often misses and cancels). Slippage is still
        # measured vs the true decision price (bdec.price_ticks), not this pad.
        MARKETABLE_PAD = 0.05
        lim = price_usd + MARKETABLE_PAD if bdec.action == 0 else price_usd - MARKETABLE_PAD
        req = LimitOrderRequest(
            symbol=self.symbol, qty=bdec.size,
            side=OrderSide.BUY if bdec.action == 0 else OrderSide.SELL,
            time_in_force=TimeInForce.IOC, limit_price=round(lim, 2))
        try:
            # TradingClient is sync HTTP — run off the websocket loop. Time the
            # submit->ack round-trip: this is the REAL broker network latency
            # (the ~ms cost that dwarfs the FPGA's 205 ns compute).
            _t = time.perf_counter()
            order = await asyncio.get_event_loop().run_in_executor(
                None, self._trading_client.submit_order, req)
            self.net_ms_samples.append((time.perf_counter() - _t) * 1000.0)
            self._open_orders[str(order.id)] = bdec.action   # track for cancel-on-new
            # attribute the fill's latency + slippage (vs the decision's limit price)
            self._pending[str(order.id)] = {"lat": getattr(bdec, "lat_ns", None),
                                            "req": bdec.price_ticks}
            log.info("PAPER ORDER PLACED id=%s status=%s", order.id, order.status)
        except Exception as e:
            log.error("Alpaca order failed: %s", e)

    async def _on_trade_update(self, data):
        """REAL fill handler — Alpaca account trade_updates stream.

        Drives the portfolio from the broker's ACTUAL executions (qty, price, fee)
        rather than an assumed fill at submit time. Account-wide stream, so it
        routes each fill to the shared portfolio by the order's own symbol.
        Robust to malformed events so a bad message can't kill the stream.
        """
        try:
            event = getattr(data, "event", None)
            if event:                           # count every outcome (fill/partial/rejected/canceled/...)
                self.order_events[event] += 1
            if event not in ("fill", "partial_fill"):
                return
            order = getattr(data, "order", None)
            if order is None:
                return
            sym = getattr(order, "symbol", None) or self.symbol
            side = str(getattr(order, "side", "")).lower()
            action = 0 if "buy" in side else 1
            # incremental fill qty/price for THIS event (fall back to order aggregates)
            qty = int(float(getattr(data, "qty", 0) or getattr(order, "filled_qty", 0) or 0))
            price = float(getattr(data, "price", 0) or getattr(order, "filled_avg_price", 0) or 0)
            if qty <= 0 or price <= 0:
                return
            oid = str(getattr(order, "id", ""))
            pend = self._pending.get(oid) or {}
            lat, req = pend.get("lat"), pend.get("req")
            # Alpaca equities are commission-free; regulatory fees settle separately
            # and are not in the fill event. Use the broker fee if the event carries
            # one, else 0.0 (a REAL fee, not the modelled fee_bps).
            fee = float(getattr(data, "fee", 0) or 0.0)
            self.portfolio.on_fill(sym, action, qty, to_ticks(price),
                                   lat_ns=lat, fee=fee, req_price_ticks=req)
            self.bridge.risk.record_fill(action, qty)
            log.info("FILL %s %s %d @ %.4f  [broker fill, fee $%.4f]",
                     "BUY" if action == 0 else "SELL", sym, qty, price, fee)
            if event == "fill":                 # terminal — clear tracking
                self._pending.pop(oid, None)
                self._open_orders.pop(oid, None)
        except Exception as e:
            log.error("trade_update handler error: %s", e)

    def _on_exec(self, trade, fill):
        """REAL fill handler — IBKR execDetailsEvent (sync callback).

        Books the broker's actual execution (shares, price, commission) into the
        portfolio instead of an assumed fill at submit."""
        try:
            ex = fill.execution
            action = 0 if getattr(ex, "side", "") == "BOT" else 1
            qty = int(getattr(ex, "shares", 0) or 0)
            price = float(getattr(ex, "price", 0) or 0)
            if qty <= 0 or price <= 0:
                return
            fee = 0.0
            cr = getattr(fill, "commissionReport", None)
            if cr is not None and getattr(cr, "commission", None):
                fee = abs(float(cr.commission))
            sym = getattr(trade.contract, "symbol", None) or self.symbol
            self.portfolio.on_fill(sym, action, qty, to_ticks(price),
                                   lat_ns=self._pending_lat, fee=fee,
                                   req_price_ticks=self._pending_req)
            self.order_events["fill"] += 1
            self.bridge.risk.record_fill(action, qty)
            log.info("FILL %s %s %d @ %.4f  [IBKR exec, fee $%.4f]",
                     "BUY" if action == 0 else "SELL", sym, qty, price, fee)
        except Exception as e:
            log.error("execDetails handler error: %s", e)

    async def _reconcile_portfolio(self):
        """Seed / repair the shared Portfolio from the broker's ACTUAL positions
        (real qty + avg entry), so equity / exposure / drawdown are broker-true —
        at startup and periodically, catching drift, restarts, or manual trades.
        Alpaca path only (needs a trading client)."""
        if self._trading_client is None:
            return
        loop = asyncio.get_event_loop()
        try:
            positions = await loop.run_in_executor(None, self._trading_client.get_all_positions)
        except Exception as e:
            log.error("portfolio reconcile failed: %s", e)
            return
        seen = set()
        for pos in positions:
            try:
                sym = pos.symbol
                qty = int(float(pos.qty))                 # signed: negative = short
                avg = float(pos.avg_entry_price)
                self.portfolio.set_position(sym, qty, avg)
                seen.add(sym)
            except Exception:
                continue
        # symbols we track but the broker no longer holds -> flat
        for sym in list(self.portfolio.books):
            if sym not in seen:
                self.portfolio.set_position(sym, 0, 0.0)
        log.info("portfolio reconciled to broker: %d open position(s)", len(seen))

    async def _periodic_reconcile(self, every=30):
        """Re-reconcile the portfolio to broker truth every `every` seconds."""
        while True:
            await asyncio.sleep(every)
            await self._reconcile_portfolio()

    async def _sync_position(self):
        """Set the risk guard's net position to the broker's ACTUAL signed qty
        for this symbol, so the inventory clamp reflects reality rather than an
        optimistic local fill count. Broker reports no position => flat (0)."""
        if self._trading_client is None:
            return
        loop = asyncio.get_event_loop()
        try:
            p = await loop.run_in_executor(
                None, self._trading_client.get_open_position, self.symbol)
            actual = int(float(p.qty))           # signed: negative = short
        except Exception:
            actual = 0                            # 404 / no position => flat
        if actual != self.bridge.risk._position:
            log.info("SYNC  position %d -> %d (broker truth)",
                     self.bridge.risk._position, actual)
            self.bridge.risk._position = actual

    async def _cancel_opposite(self, action):
        """Cancel our still-working orders on the side opposite to `action`."""
        opp = 1 - action
        victims = [oid for oid, side in self._open_orders.items() if side == opp]
        loop = asyncio.get_event_loop()
        for oid in victims:
            try:
                await loop.run_in_executor(None, self._trading_client.cancel_order_by_id, oid)
                log.info("CANCEL-ON-NEW  cancelled opposite-side order %s", oid[:8])
            except Exception:
                pass   # already filled/cancelled — fine
            self._open_orders.pop(oid, None)

    # ------------------------------------------------------------------
    # LIVE MARKET-MAKER: post resting two-sided quotes to EARN the spread
    # ------------------------------------------------------------------
    async def _requote_loop(self):
        """Every requote_s seconds, refresh our resting two-sided quotes."""
        while True:
            await asyncio.sleep(self.requote_s)
            if self.control and self.control.paused:
                continue
            try:
                await self._post_quotes()
            except Exception as e:
                log.error("requote error: %s", e)

    async def _post_quotes(self):
        """Cancel our working orders and post fresh bid+ask DAY limit orders from
        the market maker (microprice ± half-spread, inventory-skewed). Resting
        limits EARN the spread on passive fills (booked via _on_trade_update)."""
        if self._trading_client is None or self._last_market is None:
            return
        bp, bs, ap, asz = self._last_market
        inv = self.portfolio.books[self.symbol].pos if self.symbol in self.portfolio.books else 0
        q = self.mm.quote(bp, bs, ap, asz, inv)
        loop = asyncio.get_event_loop()
        # cancel-replace: clear existing working orders first
        try:
            await loop.run_in_executor(None, self._trading_client.cancel_orders)
        except Exception:
            pass
        self._open_orders.clear()
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        # Alpaca opposing-order rule (see _post_capture_quotes): the reducing side
        # is capped to |inv| so buy+sell never cross zero together; flat -> one side.
        posts = []
        bidsz = q.bid_size if q.bid_price else 0
        asksz = q.ask_size if q.ask_price else 0
        if inv > 0:
            if asksz: posts.append((OrderSide.SELL, 1, min(asksz, inv), q.ask_price))
            if bidsz: posts.append((OrderSide.BUY, 0, bidsz, q.bid_price))
        elif inv < 0:
            if bidsz: posts.append((OrderSide.BUY, 0, min(bidsz, -inv), q.bid_price))
            if asksz: posts.append((OrderSide.SELL, 1, asksz, q.ask_price))
        else:                  # flat: Alpaca allows one side -> ALTERNATE bid/ask each cycle
            self._flat_toggle = not self._flat_toggle
            if self._flat_toggle and bidsz:
                posts.append((OrderSide.BUY, 0, bidsz, q.bid_price))
            elif asksz:
                posts.append((OrderSide.SELL, 1, asksz, q.ask_price))
            elif bidsz:
                posts.append((OrderSide.BUY, 0, bidsz, q.bid_price))
        for side, action, size, price_ticks in posts:
            if size <= 0:
                continue
            price = round(price_ticks / 10_000.0, 2)
            req = LimitOrderRequest(symbol=self.symbol, qty=size, side=side,
                                    time_in_force=TimeInForce.DAY, limit_price=price)
            try:
                order = await loop.run_in_executor(None, self._trading_client.submit_order, req)
                self._open_orders[str(order.id)] = action
                self._pending[str(order.id)] = {"lat": None, "req": price_ticks}
            except Exception as e:
                if "40310000" not in str(e):
                    log.error("MM post %s failed: %s", side, e)
        log.info("MM QUOTE [%s] bid %s / ask %s  (inv %d)", self.symbol,
                 round(q.bid_price / 1e4, 2) if q.bid_price else "—",
                 round(q.ask_price / 1e4, 2) if q.ask_price else "—", inv)

    async def _post_capture_quotes(self, bp, bs, ap, asz):
        """A WIDE spread appeared — immediately post bid+ask JUST INSIDE the market
        to capture most of it. Improve each side by 1 cent, skew by inventory, and
        respect the position cap. This is the 'good spread -> act now' behaviour."""
        if self._trading_client is None:
            return
        inv = self.portfolio.books[self.symbol].pos if self.symbol in self.portfolio.books else 0
        improve = 100                                   # post 1 cent inside each side
        skew = int(round(inv * self.mm.cfg.skew_ticks_per_share))
        bid = bp + improve - skew
        ask = ap - improve - skew
        if ask - bid < 100:                             # keep >=1c of our own edge
            return
        size = self.mm.cfg.quote_size
        cap = self.mm.cfg.max_position
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._trading_client.cancel_orders)
        except Exception:
            pass
        self._open_orders.clear()
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        # Alpaca forbids a buy + sell open together that would cross zero. So the
        # side that would REDUCE inventory is capped to |inv| (never flips to the
        # other side while the entry order is open); when flat we can only quote
        # one side at a time (post the side toward desired position).
        posts = []
        buy_ok = inv + size <= cap
        sell_ok = inv - size >= -cap
        if inv > 0:            # long: SELL reduces (cap to inv), BUY adds
            if sell_ok: posts.append((OrderSide.SELL, 1, min(size, inv), ask))
            if buy_ok:  posts.append((OrderSide.BUY, 0, size, bid))
        elif inv < 0:          # short: BUY reduces (cap to |inv|), SELL adds
            if buy_ok:  posts.append((OrderSide.BUY, 0, min(size, -inv), bid))
            if sell_ok: posts.append((OrderSide.SELL, 1, size, ask))
        else:                  # flat: one side at a time -> ALTERNATE bid/ask each capture
            self._flat_toggle = not self._flat_toggle
            posts.append((OrderSide.BUY, 0, size, bid) if self._flat_toggle
                         else (OrderSide.SELL, 1, size, ask))
        for side, action, sz, pt in posts:
            if sz <= 0:
                continue
            price = round(pt / 10_000.0, 2)
            req = LimitOrderRequest(symbol=self.symbol, qty=sz, side=side,
                                    time_in_force=TimeInForce.DAY, limit_price=price)
            try:
                order = await loop.run_in_executor(None, self._trading_client.submit_order, req)
                self._open_orders[str(order.id)] = action
                self._pending[str(order.id)] = {"lat": None, "req": pt}
            except Exception as e:
                if "40310000" not in str(e):    # opposing-order rule = expected, quiet
                    log.error("MM capture post failed: %s", e)
        log.info("MM CAPTURE [%s] wide spread %.2f -> post bid %.2f / ask %.2f (inv %d)",
                 self.symbol, (ap - bp) / 1e4, bid / 1e4, ask / 1e4, inv)

    def _report(self):
        s = self.stats
        log.info("==== SESSION SUMMARY [%s] ====", self.symbol)
        log.info("ticks processed : %d", s["ticks"])
        log.info("signals         : %d  (BUY=%d  SELL=%d)", s["signals"], s["buy"], s["sell"])
        log.info("net position    : %d shares", self.bridge.risk._position)
        if s["signals"] > 0:
            mean_ns = s["lat_ns_sum"] // s["signals"]
            log.info("---- TICK-TO-SIGNAL LATENCY (compute only) ----")
            log.info("FPGA  (sim-measured) : %d ns", FPGA_LATENCY_NS)
            log.info("SW    (this host)    : %d ns mean  (min %d / max %d)",
                     mean_ns, s["lat_ns_min"], s["lat_ns_max"])
            log.info("speedup (FPGA vs SW) : %.0fx faster on hardware",
                     mean_ns / FPGA_LATENCY_NS)
            log.info("(+ live order path adds network RTT ~tens of ms — the gap that matters)")

    # ------------------------------------------------------------------
    # Alpaca live path — FREE real-time IEX quotes, no IB Gateway.
    # Same strategy/risk core (_process); orders stay DRY-RUN (logged only).
    # Keys come from env: ALPACA_API_KEY / ALPACA_SECRET_KEY (paper).
    # ------------------------------------------------------------------
    async def alpaca_run(self, duration=60):
        api_key = os.environ.get("ALPACA_API_KEY")
        secret  = os.environ.get("ALPACA_SECRET_KEY")
        if not api_key or not secret:
            log.error("Set ALPACA_API_KEY and ALPACA_SECRET_KEY env vars "
                      "(free paper keys from alpaca.markets).")
            return

        from alpaca.data.live import StockDataStream
        from alpaca.data.enums import DataFeed

        # Free tier => IEX feed. (SIP requires a paid subscription.)
        stream = StockDataStream(api_key, secret, feed=DataFeed.IEX)

        # Paper trading client only when actually executing.
        self._trading_client = None
        if self.execute:
            from alpaca.trading.client import TradingClient
            self._trading_client = TradingClient(api_key, secret, paper=True)
            acct = self._trading_client.get_account()
            log.info("Alpaca PAPER account %s — buying power $%s",
                     acct.account_number, acct.buying_power)

        if self.momentum:
            # MOMENTUM TAKER: when the mid ticks UP and a real spread exists, BUY
            # BIG (order_size, >1000) — ride the move. Directional & aggressive.
            async def on_quote(q):
                bp, ap = to_ticks(q.bid_price or 0), to_ticks(q.ask_price or 0)
                if bp <= 0 or ap <= 0:
                    return
                mid = (bp + ap) // 2
                self.portfolio.mark_price(self.symbol, mid)
                self.portfolio.set_quote(self.symbol, bp, ap)
                self.stats["ticks"] += 1
                prev = self._prev_mid
                up = prev is not None and mid > prev
                spread = ap - bp
                self._prev_mid = mid
                if up and spread > 0:
                    # tight spread = cheap to cross & fills immediately -> size UP
                    size = self.order_size
                    tight = self.tight_ticks and spread <= self.tight_ticks
                    if tight:
                        size = int(self.order_size * self.size_boost)
                    t0 = time.perf_counter_ns()
                    dec = BridgeDecision(action=0, price_ticks=ap, size=size)  # BUY at ask
                    dec.lat_ns = time.perf_counter_ns() - t0
                    self.lat_samples.append(dec.lat_ns)
                    self.stats["signals"] += 1
                    log.info("MOMENTUM UP %.2f>%.2f spread %.2f%s -> BUY %d @ ask %.2f",
                             mid/1e4, prev/1e4, spread/1e4, " [TIGHT x%.1f]" % self.size_boost if tight else "",
                             size, ap/1e4)
                    await self._alpaca_submit(dec)
        elif self.maker:
            # MARKET-MAKER: quotes only update our market view; a separate loop
            # posts resting two-sided limit orders to EARN the spread.
            async def on_quote(q):
                bp, ap = to_ticks(q.bid_price or 0), to_ticks(q.ask_price or 0)
                if bp <= 0 or ap <= 0:
                    return
                bs, asz = int(q.bid_size or 0), int(q.ask_size or 0)
                self._last_market = (bp, bs, ap, asz)
                mid = (bp + ap) // 2
                self.portfolio.mark_price(self.symbol, mid)
                self.portfolio.set_quote(self.symbol, bp, ap)
                # GOOD (wide) SPREAD -> immediately post INSIDE it to capture it,
                # rather than waiting for the periodic requote. Throttled by requote_s.
                if (self.execute and not (self.control and self.control.paused)
                        and self.good_spread_ticks and (ap - bp) >= self.good_spread_ticks
                        and (time.time() - self._last_capture) >= self.requote_s):
                    self._last_capture = time.time()
                    await self._post_capture_quotes(bp, bs, ap, asz)
        else:
            async def on_quote(q):
                # TAKER: run each quote through the signal + gauntlet, cross the spread.
                dec = self._process(q.bid_price, q.bid_size, q.ask_price, q.ask_size)
                if dec is not None:
                    await self._alpaca_submit(dec)

        stream.subscribe_quotes(on_quote, self.symbol)

        # EXECUTE: also run the account trade-updates stream so REAL fills drive
        # the portfolio (positions / PnL / trades / fees), not assumed fills.
        tstream = None
        if self.execute:
            from alpaca.trading.stream import TradingStream
            tstream = TradingStream(api_key, secret, paper=True)
            tstream.subscribe_trade_updates(self._on_trade_update)
            # seed the portfolio (broker-true positions) + risk guard now
            await self._reconcile_portfolio()
            await self._sync_position()

        log.info("ALPACA live IEX quotes for %s. %s orders. Running %ds ...",
                 self.symbol,
                 "EXECUTING paper (real fills)" if self.execute else "DRY-RUN (logged, not placed)",
                 duration)
        try:
            # _run_forever() is the coroutine behind the blocking .run(); run the
            # quote stream (and, when executing, the trade-updates stream) together.
            coros = [stream._run_forever()]
            if tstream is not None:
                coros.append(tstream._run_forever())
                coros.append(self._periodic_reconcile())   # keep portfolio broker-true
                if self.maker:
                    coros.append(self._requote_loop())      # post/refresh resting quotes
            await asyncio.wait_for(asyncio.gather(*coros), timeout=duration)
        except asyncio.TimeoutError:
            log.info("Duration reached — shutting down")
        except (KeyboardInterrupt, asyncio.CancelledError):
            log.info("Interrupted — shutting down")
        finally:
            # AUTO-FLATTEN on stop: never leave a position open when the session ends.
            if self.execute:
                log.info("session ending — flattening open positions")
                await _flatten_all([self])
            for st in (stream, tstream):
                if st is None:
                    continue
                try:
                    await st.stop_ws()
                except Exception:
                    pass
            self._report()

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
                await self._send(dec)
            await asyncio.sleep(0.05)
        self._report()

    # ------------------------------------------------------------------
    # Local live simulator — a continuous synthetic quote feed so the full
    # prototype runs end-to-end on this machine with no IBKR connection.
    # Random-walk mid + fluctuating top-of-book sizes with occasional
    # imbalance bursts (the conditions the strategy fires on).
    # ------------------------------------------------------------------
    async def simulate(self, duration=20, rate=20, seed=1):
        from quote_sim import gen_quotes
        # Offset the seed per symbol so a multi-symbol sim produces a distinct
        # (but reproducible) stream for each name instead of 7 identical ones.
        eff_seed = seed + (sum(ord(c) for c in self.symbol) if self.symbol else 0)
        mode = "MARKET-MAKER" if self.maker else "TAKER"
        log.info("LOCAL LIVE SIM [%s] %s — synthetic quote stream (%d ticks/s for %ds, "
                 "no Gateway). %s orders.", self.symbol, mode, rate, duration,
                 "EXECUTING paper" if self.execute else "DRY-RUN (logged, not placed)")
        if self.maker:
            await self._simulate_maker(gen_quotes(duration * rate, eff_seed), rate, eff_seed)
            return
        for (bid, bs, ask, asz) in gen_quotes(duration * rate, eff_seed):
            dec = self._process(bid, bs, ask, asz)
            if dec is not None:
                await self._send(dec)
            await asyncio.sleep(1.0 / rate)
        self._report()

    async def _simulate_maker(self, quotes, rate, seed):
        """Market-making simulation: post two-sided quotes, fill via noise flow,
        book PnL in the shared portfolio. Same fill model as host/backtest.py
        (spread capture + inventory marked to the moving mid). DRY-RUN only."""
        import random
        rng = random.Random(seed)
        fills = 0
        for (bid, bs, ask, asz) in quotes:
            # honour the firm-wide kill switch: once daily-loss/drawdown trips,
            # stop quoting for the rest of the session (same rule the gauntlet
            # applies to the taker).
            if self.portfolio.halted:
                log.warning("MM HALT [%s] — kill switch tripped, stop quoting: %s",
                            self.symbol, self.portfolio.halt_reason)
                break
            bp, ap = to_ticks(bid), to_ticks(ask)
            mid = (bp + ap) // 2
            # mark to the current mid FIRST so any fill's PnL (and the kill-switch
            # check inside on_fill) uses a real price, not an unset mark.
            self.portfolio.mark_price(self.symbol, mid)
            self.portfolio.set_quote(self.symbol, bp, ap)
            inv = self.portfolio.books[self.symbol].pos
            # time the quote decision — the maker's compute-latency analog of the
            # taker's tick->signal time (the number the FPGA does in 205 ns).
            t0 = time.perf_counter_ns()
            q = self.mm.quote(bp, bs, ap, asz, inv)
            lat_ns = time.perf_counter_ns() - t0
            self.lat_samples.append(lat_ns)
            if q.bid_size and q.bid_price and rng.random() < 0.25:
                self.portfolio.on_fill(self.symbol, 0, q.bid_size, q.bid_price, lat_ns=lat_ns)
                fills += 1
                log.info("MM FILL [%s] BUY  %d @ %.4f  (inv %d)  [quote %d ns]",
                         self.symbol, q.bid_size, q.bid_price / 1e4, self.portfolio.books[self.symbol].pos, lat_ns)
            if q.ask_size and q.ask_price and rng.random() < 0.25:
                self.portfolio.on_fill(self.symbol, 1, q.ask_size, q.ask_price, lat_ns=lat_ns)
                fills += 1
                log.info("MM FILL [%s] SELL %d @ %.4f  (inv %d)  [quote %d ns]",
                         self.symbol, q.ask_size, q.ask_price / 1e4, self.portfolio.books[self.symbol].pos, lat_ns)
            await asyncio.sleep(1.0 / rate)
        b = self.portfolio.books.get(self.symbol)
        log.info("==== MM SESSION [%s] ==== fills=%d  pos=%d  realized=$%.2f  unreal=$%.2f",
                 self.symbol, fills, b.pos if b else 0,
                 b.realized if b else 0.0, b.unrealized() if b else 0.0)


def resolve_symbols(args) -> list:
    """Pick the symbol list from --symbols / --universe / --symbol (in that order)."""
    if args.symbols:
        return [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    if args.universe:
        key = args.universe.lower()
        if key not in UNIVERSES:
            raise SystemExit(f"unknown --universe '{args.universe}'; "
                             f"choices: {', '.join(UNIVERSES)}")
        return list(UNIVERSES[key])
    return [args.symbol.upper()]


async def _run_feed(feed: "LiveFeed", args):
    """Dispatch one feed to the mode selected on the command line."""
    if args.alpaca:
        await feed.alpaca_run(duration=args.duration)
    elif args.demo:
        await feed.demo()
    elif args.simulate:
        await feed.simulate(duration=args.duration, rate=args.rate, seed=args.seed)
    else:
        await feed.run(duration=args.duration)


async def _run_all(feeds, args):
    # return_exceptions so one symbol failing (e.g. a bad qualify) doesn't kill
    # the rest; surface any failures afterwards.
    results = await asyncio.gather(*(_run_feed(f, args) for f in feeds),
                                   return_exceptions=True)
    for feed, res in zip(feeds, results):
        if isinstance(res, Exception):
            log.error("feed for %s failed: %s", feed.symbol, res)


def main():
    p = argparse.ArgumentParser(description="IBKR live market data -> strategy -> paper orders")
    p.add_argument('--symbol',    default='AAPL', help='single symbol (default)')
    p.add_argument('--symbols',   default=None, help='comma-separated symbols, e.g. AAPL,MSFT,NVDA (overrides --symbol)')
    p.add_argument('--universe',  default=None, help=f'named symbol set, one of: {", ".join(UNIVERSES)} (overrides --symbol)')
    p.add_argument('--host',      default='127.0.0.1')
    p.add_argument('--port',      type=int, default=4002, help='IB Gateway paper=4002, TWS paper=7497')
    p.add_argument('--client-id', type=int, default=10, help='base IB clientId; each extra symbol uses base+i')
    p.add_argument('--realtime',  action='store_true', help='real-time data (needs subscription); default delayed/free')
    p.add_argument('--execute',   action='store_true', help='actually place PAPER orders (default: dry-run, logged only)')
    p.add_argument('--threshold', type=int, default=15, help='imbalance threshold *10 (15 = 1.5x)')
    p.add_argument('--duration',  type=int, default=60, help='seconds to run')
    p.add_argument('--alpaca',    action='store_true', help='FREE Alpaca live IEX quotes (no IB Gateway; needs ALPACA_API_KEY/ALPACA_SECRET_KEY)')
    p.add_argument('--demo',      action='store_true', help='offline 6-tick demo (no Gateway)')
    p.add_argument('--simulate',  action='store_true', help='local continuous live-quote sim (no Gateway)')
    p.add_argument('--maker',     action='store_true', help='market-making mode: post two-sided quotes & earn the spread (with --simulate)')
    p.add_argument('--rate',      type=int, default=20, help='sim ticks/sec (with --simulate)')
    p.add_argument('--seed',      type=int, default=1, help='sim RNG seed (with --simulate)')
    p.add_argument('--serve-port', type=int, default=8000, help='port to serve the live monitor + control endpoint')
    p.add_argument('--fee-bps',   type=float, default=1.0, help='gauntlet edge-vs-cost fee (bps); lower = more trades pass')
    p.add_argument('--min-edge',  type=int, default=1, help='gauntlet fair-value min edge in ticks (microprice vs mid)')
    p.add_argument('--min-profit-ticks', type=int, default=0, help='extra edge (ticks) required beyond fee before trading')
    # per-position risk management (ON by default; 0 disables)
    p.add_argument('--stop-loss',   type=float, default=50.0, help='flatten a symbol if its unrealized PnL <= -$X (0=off)')
    p.add_argument('--take-profit', type=float, default=75.0, help='flatten a symbol if its unrealized PnL >= +$X (0=off)')
    p.add_argument('--cooldown',    type=float, default=2.0, help='min seconds between entries per symbol (anti-churn)')
    # live market-making (post resting two-sided quotes to earn the spread)
    p.add_argument('--half-spread-cents', type=float, default=3.0, help='maker half-spread in cents (each quote from mid)')
    p.add_argument('--quote-size',  type=int, default=100, help='shares per maker quote side')
    p.add_argument('--requote',     type=float, default=3.0, help='seconds between maker quote refreshes')
    p.add_argument('--skew',        type=float, default=2.0, help='maker inventory skew (ticks/share); higher = revert to flat faster')
    p.add_argument('--max-inventory', type=int, default=300, help='maker max |inventory| — keep it small')
    p.add_argument('--max-hold',    type=float, default=45.0, help='flatten any position held longer than N sec (0=off)')
    p.add_argument('--good-spread-cents', type=float, default=5.0, help='maker: spread >= this (cents) = good -> immediately post inside to capture')
    # momentum taker (directional): buy big on an up-move
    p.add_argument('--momentum',    action='store_true', help='MOMENTUM TAKER: buy big when price ticks up (directional, aggressive)')
    p.add_argument('--order-size',  type=int, default=1200, help='shares per momentum buy (>1000)')
    p.add_argument('--size-boost',  type=float, default=2.0, help='multiply order size when the spread is tight (good immediate fill)')
    p.add_argument('--tight-spread-cents', type=float, default=3.0, help='spread <= this (cents) counts as a tight/good spread -> size up')
    args = p.parse_args()

    symbols = resolve_symbols(args)

    # ONE shared portfolio + ONE shared pre-trade gauntlet across all symbols, so
    # exposure limits, the kill switch, and the funnel are firm-wide (the gauntlet
    # keeps per-symbol state internally, keyed by symbol).
    portfolio = Portfolio(fee_bps=args.fee_bps)
    gauntlet  = PreTradeGauntlet(portfolio, HFTConfig(fee_bps=args.fee_bps,
                                                      min_edge_ticks=args.min_edge,
                                                      min_profit_ticks=args.min_profit_ticks))

    # One LiveFeed per symbol. Each IB session needs a unique clientId, so the
    # i-th symbol connects with base client_id + i.
    if args.momentum:
        maker_mode = False               # momentum takes precedence over maker
    elif args.maker and not (args.simulate or args.alpaca):
        log.warning("--maker is wired for --simulate and --alpaca; ignoring for this mode")
        maker_mode = False
    else:
        maker_mode = args.maker
    control = LiveControl()
    feeds = [LiveFeed(symbol=sym, host=args.host, port=args.port,
                      client_id=args.client_id + i, realtime=args.realtime,
                      execute=args.execute, threshold=args.threshold,
                      portfolio=portfolio, gauntlet=gauntlet,
                      maker=maker_mode, control=control,
                      stop_loss=args.stop_loss, take_profit=args.take_profit,
                      cooldown_s=args.cooldown, half_spread_cents=args.half_spread_cents,
                      quote_size=args.quote_size, requote_s=args.requote, skew=args.skew,
                      max_inventory=args.max_inventory, max_hold_s=args.max_hold,
                      good_spread_cents=args.good_spread_cents,
                      momentum=args.momentum, order_size=args.order_size,
                      size_boost=args.size_boost, tight_spread_cents=args.tight_spread_cents)
             for i, sym in enumerate(symbols)]

    # live monitor plumbing: capture logs + write run_state.json each second
    import time as _time
    logring = LogRing()
    logging.getLogger().addHandler(logring)
    source = ("DEMO" if args.demo else "SIM" if args.simulate
              else "ALPACA" if args.alpaca else "IBKR")
    meta = {"mode": "momentum" if args.momentum else ("maker" if maker_mode else "taker"), "symbols": symbols,
            "execute": bool(args.execute), "started": _time.time(), "source": source}

    # serve the page + accept control POSTs (pause/resume/flatten/stop) on :serve_port
    _start_control_server(control, feeds, port=args.serve_port)

    async def _run_with_monitor():
        writer = asyncio.create_task(
            _live_state_writer(portfolio, gauntlet, feeds, meta, logring, control))
        try:
            if len(feeds) == 1:
                await _run_feed(feeds[0], args)
            else:
                log.info("Trading %d symbols concurrently: %s", len(symbols), ", ".join(symbols))
                await _run_all(feeds, args)
        finally:
            writer.cancel()
            try:
                await writer
            except asyncio.CancelledError:
                pass

    log.info("open the live monitor at http://localhost:%d/live.html "
             "(pause/flatten/stop from the page)", args.serve_port)
    asyncio.run(_run_with_monitor())

    # Firm-wide wrap-up: the decision funnel + the portfolio book.
    for line in gauntlet.funnel_lines():
        log.info(line)
    for line in portfolio.summary_lines():
        log.info(line)

    _write_run_summary(feeds, portfolio, gauntlet)


def _write_run_summary(feeds, portfolio, gauntlet):
    """Persist a run's data (funnel, equity curve, per-symbol book, latency
    samples) to reports/run_summary.json so host/dashboard.py can chart it."""
    import json, os
    reports = os.path.join(os.path.dirname(__file__), '..', 'reports')
    os.makedirs(reports, exist_ok=True)
    lat = [n for f in feeds for n in f.lat_samples]
    summary = {
        "fpga_ns": FPGA_LATENCY_NS,
        "funnel": dict(gauntlet.stats),
        "equity_curve": portfolio.equity_curve,
        "latency_ns": lat,
        "books": {sym: {"pos": b.pos, "realized": round(b.realized, 2),
                        "unrealized": round(b.unrealized(), 2)}
                  for sym, b in portfolio.books.items()},
        "gross_exposure": round(portfolio.gross_exposure(), 2),
        "net_exposure": round(portfolio.net_exposure(), 2),
        "equity": round(portfolio.equity(), 2),
    }
    path = os.path.join(reports, 'run_summary.json')
    with open(path, 'w') as fh:
        json.dump(summary, fh, indent=2)
    log.info("run summary written to %s", os.path.normpath(path))


# --------------------------------------------------------------------------
# Live monitor: a self-refreshing web page (reports/live.html) reads the
# reports/run_state.json this writes ~once a second while the feed runs.
# --------------------------------------------------------------------------
class LogRing(logging.Handler):
    """Keeps the last N formatted log lines in memory for the live page."""
    def __init__(self, capacity=60):
        super().__init__()
        from collections import deque
        self.lines = deque(maxlen=capacity)
        self.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))

    def emit(self, record):
        try:
            self.lines.append(self.format(record))
        except Exception:
            pass


class LiveControl:
    """Shared control flags the web page sets and the feed obeys."""
    def __init__(self):
        self.paused = False       # skip placing new orders (position kept)
        self.flatten_req = False  # close all positions on the next writer tick
        self._flattened_on_halt = False


def _reports_dir():
    import os
    return os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'reports'))


def _start_control_server(control, feeds, port=8000):
    """Serve reports/ (the page) AND accept POST /control so the LIVE feed can be
    paused / resumed / flattened / stopped from the browser — no more manual
    closing in the broker UI. Runs in a daemon thread."""
    import json, threading
    from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
    reports = _reports_dir()

    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k): super().__init__(*a, directory=reports, **k)
        def log_message(self, *a): pass
        def do_GET(self):
            if self.path == '/':
                self.path = '/live.html'
            return super().do_GET()
        def do_POST(self):
            if self.path.split('?')[0] != '/control':
                self.send_error(404); return
            try:
                n = int(self.headers.get('Content-Length', 0))
                cmd = (json.loads(self.rfile.read(n) or b'{}')).get('cmd')
                if cmd == 'pause':      control.paused = True
                elif cmd == 'resume':   control.paused = False
                elif cmd == 'flatten':  control.flatten_req = True
                elif cmd == 'stop':     control.paused = True; control.flatten_req = True
                elif cmd == 'reset_kill':
                    for f in feeds:
                        f.portfolio.halted = False; f.portfolio.halt_reason = None
                    control._flattened_on_halt = False
                else:
                    raise ValueError('unknown cmd')
                res = {'ok': True}
            except Exception as e:
                res = {'ok': False, 'error': str(e)}
            body = json.dumps(res).encode()
            self.send_response(200 if res['ok'] else 400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body))); self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(('0.0.0.0', port), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log.info("live monitor + control served at http://localhost:%d/live.html", port)
    return httpd


async def _close_symbol(feed, symbol):
    """Close one symbol's position at market (stop-loss / take-profit)."""
    tc = getattr(feed, '_trading_client', None)
    if tc is None:
        return
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: tc.close_position(symbol))
    except Exception as e:
        log.error("close_position(%s) failed: %s", symbol, e)


async def _enforce_position_risk(feeds):
    """Per-position STOP-LOSS / TAKE-PROFIT: flatten a symbol whose unrealized PnL
    breaches its stop or target. Caps the loss tail and locks winners — the fix
    for 'losers run, winners get cut'. Execute paths only."""
    import time as _t
    for f in feeds:
        if not f.execute:
            continue
        b = f.portfolio.books.get(f.symbol)
        if not b or b.pos == 0:
            f._risk_closing = False             # flat -> re-arm the stop/target
            f._pos_since = None
            continue
        if f._pos_since is None:
            f._pos_since = _t.time()            # position just opened
        if f._risk_closing:
            continue
        u = b.unrealized()
        hit = None
        if f.stop_loss and u <= -abs(f.stop_loss):
            hit = "STOP-LOSS"
        elif f.take_profit and u >= abs(f.take_profit):
            hit = "TAKE-PROFIT"
        elif f.max_hold_s and (_t.time() - f._pos_since) > f.max_hold_s:
            hit = "MAX-HOLD"                    # a MM shouldn't sit on inventory
        if hit:
            f._risk_closing = True
            log.warning("%s [%s] unrealized $%.2f -> closing position", hit, f.symbol, u)
            await _close_symbol(f, f.symbol)
            f._last_trade_t = time.time()       # also start the cooldown after a stop/target


async def _flatten_all(feeds):
    """Close ALL open positions + cancel working orders on the broker (real
    execute paths). The subsequent fills/reconcile update the portfolio."""
    for f in feeds:
        tc = getattr(f, '_trading_client', None)
        if tc is not None:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: tc.close_all_positions(cancel_orders=True))
                log.info("FLATTEN: close_all_positions(cancel_orders=True) sent")
            except Exception as e:
                log.error("flatten failed: %s", e)
            return
    log.info("FLATTEN requested (no live trading client — nothing to close)")


def _percentile(vals, p):
    if not vals:
        return 0
    s = sorted(vals)
    return s[min(len(s) - 1, int(p * len(s)))]


def _trade_stats(portfolio):
    """Win rate / avg win-loss / fees / best-worst symbol from the trade book."""
    closes = [t["pnl"] for t in portfolio.trades if t.get("pnl")]  # realizing fills
    wins = [p for p in closes if p > 0]
    losses = [p for p in closes if p < 0]
    import statistics
    best = worst = None
    if portfolio.books:
        ranked = sorted(portfolio.books.items(),
                        key=lambda kv: kv[1].realized + kv[1].unrealized())
        worst = {"symbol": ranked[0][0],
                 "pnl": round(ranked[0][1].realized + ranked[0][1].unrealized(), 2)}
        best = {"symbol": ranked[-1][0],
                "pnl": round(ranked[-1][1].realized + ranked[-1][1].unrealized(), 2)}
    return {
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(100 * len(wins) / (len(wins) + len(losses)), 1) if closes else 0.0,
        "avg_win": round(statistics.fmean(wins), 2) if wins else 0.0,
        "avg_loss": round(statistics.fmean(losses), 2) if losses else 0.0,
        "fees": round(portfolio.fees, 2),
        "equity_net": round(portfolio.equity() - portfolio.fees, 2),
        "best": best, "worst": worst,
    }


def _write_live_state(portfolio, gauntlet, feeds, meta, logring, control=None):
    """Serialize the FULL front-page dataset from the REAL live objects.

    Every field is derived from live trades / real config — no synthetic values.
    Persistent per-write state (peak PnL for drawdown, per-interval latency series,
    events) is carried in `meta` so it accumulates across the 1 Hz writes.
    """
    import json, os, time, statistics
    from collections import deque
    reports = os.path.join(os.path.dirname(__file__), '..', 'reports')
    os.makedirs(reports, exist_ok=True)

    # --- lazily init persistent trackers on the first write ---
    if "peaks" not in meta:
        meta["peaks"] = {}                       # symbol -> peak (realized+unreal) PnL
        meta["series"] = deque(maxlen=120)       # per-write median SW latency (µs)
        meta["lat_seen"] = 0                     # samples consumed into the series so far
        meta["events"] = deque(maxlen=40)
        meta["was_halted"] = False
        meta["events"].appendleft({"time": time.strftime("%H:%M:%S"),
                                   "msg": "session started · source=%s mode=%s (%s)" % (
                                       meta.get("source", "?"), meta["mode"],
                                       "EXECUTE" if meta["execute"] else "dry-run"),
                                   "level": "info"})

    lat = [n for f in feeds for n in f.lat_samples]
    net = [m for f in feeds for m in f.net_ms_samples]
    import collections as _c
    _oe = dict(sum((f.order_events for f in feeds), _c.Counter()))

    # per-symbol peak PnL -> drawdown (real, tracked over the session)
    for s, b in portfolio.books.items():
        pnl = b.realized + b.unrealized()
        meta["peaks"][s] = max(meta["peaks"].get(s, pnl), pnl)

    # per-interval latency median for the "median over time" line
    if len(lat) > meta["lat_seen"]:
        new = lat[meta["lat_seen"]:]
        meta["series"].append(round(statistics.median(new) / 1000, 2))
        meta["lat_seen"] = len(lat)

    # kill-switch transition -> event
    if portfolio.halted and not meta["was_halted"]:
        meta["events"].appendleft({"time": time.strftime("%H:%M:%S"),
                                   "msg": "KILL SWITCH tripped: %s" % portfolio.halt_reason,
                                   "level": "alert"})
    meta["was_halted"] = portfolio.halted

    # real config read straight off the live objects (strategy / maker / portfolio)
    strat = feeds[0].strategy if feeds else None
    mm = feeds[0].mm if feeds else None
    cfg = {
        "half_spread": mm.cfg.half_spread_ticks if mm else 0,
        "fee_bps": portfolio.fee_bps,
        "gross_cap": portfolio.max_gross_notional,
        "daily_loss": portfolio.daily_loss_limit,
    }

    state = {
        "updated": time.strftime("%H:%M:%S"),
        "status": {
            "mode": meta["mode"], "symbols": meta["symbols"],
            "execute": meta["execute"], "source": meta.get("source", "?"),
            "uptime_s": round(time.time() - meta["started"], 1),
            "halted": portfolio.halted, "halt_reason": portfolio.halt_reason,
            "running": True, "paused": bool(control.paused) if control else False,
            "threshold": strat.BID_THRESH if strat else 0,
            "config": cfg,
        },
        "pnl": {
            "realized": round(portfolio.realized(), 2),
            "unrealized": round(portfolio.unrealized(), 2),
            "equity": round(portfolio.equity(), 2),
            "net_exposure": round(portfolio.net_exposure(), 0),
            "gross_exposure": round(portfolio.gross_exposure(), 0),
            "gross_cap": portfolio.max_gross_notional,
        },
        "books": [
            {"symbol": s, "pos": b.pos, "avg": round(b.avg_cost, 2),
             "mark": round(b.mark, 2), "realized": round(b.realized, 2),
             "unrealized": round(b.unrealized(), 2),
             "bid": round(b.bid, 2), "ask": round(b.ask, 2),
             "drawdown": round(max(0.0, meta["peaks"].get(s, 0.0)
                                   - (b.realized + b.unrealized())), 2),
             "active": True}
            for s, b in sorted(portfolio.books.items())
        ],
        "stats": portfolio.trade_stats(),
        "orders": _oe,
        "fill_ratio": round(100 * ((_oe.get("fill", 0) + _oe.get("partial_fill", 0))
                                   / max(1, _oe.get("new", 0))), 1),
        "trades": [
            {"time": time.strftime("%H:%M:%S", time.localtime(t["t"])),
             "symbol": t["symbol"], "side": t["side"],
             "size": t["size"], "price": t["price"], "lat_ns": t.get("lat_ns"),
             "pnl": t.get("pnl"), "slip": t.get("slip")}
            for t in portfolio.trades[-25:][::-1]
        ],
        "n_trades": len(portfolio.trades),
        "funnel": dict(gauntlet.stats),
        "latency": {
            "n": len(lat),
            "sum_ns": sum(lat),
            "median_ns": int(statistics.median(lat)) if lat else 0,
            "p50_ns": int(_percentile(lat, 0.50)),
            "p90_ns": int(_percentile(lat, 0.90)),
            "p99_ns": int(_percentile(lat, 0.99)),
            "max_ns": max(lat) if lat else 0,
            "fpga_ns": FPGA_LATENCY_NS,          # reference (hardware pipeline, sim-measured)
            "net_ms": round(statistics.median(net), 1) if net else 0,  # REAL order submit->ack RTT
            "samples": lat[-400:],               # recent raw samples for the live histogram
            "series": list(meta["series"]),
        },
        "equity_curve": portfolio.equity_curve[-200:],
        "realized_curve": portfolio.realized_curve[-200:],
        "events": list(meta["events"]),
        "log": list(logring.lines) if logring else [],
    }
    tmp = os.path.join(reports, 'run_state.json.tmp')
    with open(tmp, 'w') as fh:
        json.dump(state, fh)
    os.replace(tmp, os.path.join(reports, 'run_state.json'))


async def _live_state_writer(portfolio, gauntlet, feeds, meta, logring, control=None, period=1.0):
    """Refresh run_state.json every `period` seconds; also act on control flags
    (flatten on request or on a kill-switch trip) until cancelled."""
    try:
        while True:
            # page-requested flatten
            if control and control.flatten_req:
                control.flatten_req = False
                await _flatten_all(feeds)
            # AUTO-FLATTEN on kill-switch trip (once), then pause trading
            if control and portfolio.halted and not control._flattened_on_halt:
                control._flattened_on_halt = True
                control.paused = True
                log.warning("kill switch tripped — flattening and pausing")
                await _flatten_all(feeds)
            # per-position stop-loss / take-profit
            await _enforce_position_risk(feeds)
            _write_live_state(portfolio, gauntlet, feeds, meta, logring, control)
            await asyncio.sleep(period)
    except asyncio.CancelledError:
        _write_live_state(portfolio, gauntlet, feeds, meta, logring, control)
        raise


if __name__ == '__main__':
    main()
