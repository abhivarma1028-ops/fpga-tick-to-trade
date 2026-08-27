"""
Live monitor CONTROL server — turns the read-only dashboard into a control panel.

Runs a controllable trading simulation IN-PROCESS and serves the monitor page,
so the browser can start/stop/pause it, switch taker<->maker, change the imbalance
threshold, and reset the kill switch — all live. Dependency-free (stdlib only).

  GET  /                 -> reports/live.html
  GET  /run_state.json   -> current live state (written each ~0.5 s)
  GET  /<file>           -> other files under reports/
  POST /control          -> {"cmd": "...", ...}  (see Controller.command)

Run:  python host/monitor_server.py            # http://localhost:8000/live.html
      python host/monitor_server.py --port 8000 --rate 20

Reuses the real components (SoftwareStrategy, PreTradeGauntlet, Portfolio,
MarketMaker, quote_sim-style random walk) so the numbers match the rest of the
project. This is a SIMULATION control panel (synthetic quotes); live IBKR/Alpaca
data still runs through live_feed.py.
"""

import os
import json
import time
import random
import argparse
import threading
import statistics
from collections import deque
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

import sys
sys.path.insert(0, os.path.dirname(__file__))
from portfolio import Portfolio, TICKS_PER_USD
from hft_logic import PreTradeGauntlet, HFTConfig
from market_maker import MarketMaker, MMConfig
from strategy_sw import SoftwareStrategy
from strategy_registry import StrategyBook, AVAILABLE as STRATS_AVAILABLE
from crypto_feed import CryptoFeed, TICKS_PER_USD as FEED_TICKS, SIZE_SCALE
from alpaca_bridge import AlpacaBridge, CryptoRiskConfig, Decision as BrDecision

FPGA_NS = 205
MAG7 = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]
REPORTS = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "reports"))


class Controller:
    """Holds all sim state and reacts to control commands from the web page."""

    def __init__(self, symbols=None, rate=20, seed=1):
        self.symbols = symbols or MAG7
        self.rate = rate
        # daily-loss limit sized for a 7-symbol book; maker half-spread tuned to
        # ~break-even after fees (the backtest showed 3 ticks bleeds, ~200 earns).
        self.portfolio = Portfolio(daily_loss_limit_usd=50_000.0)
        self.gauntlet = PreTradeGauntlet(self.portfolio, HFTConfig())
        self.mm = MarketMaker(MMConfig(half_spread_ticks=200))
        self.mode = "maker"            # or "taker"
        self.threshold = 15
        self.running = True            # master on/off
        self.paused = False
        self.started = time.time()
        self._rng = random.Random(seed)
        self._mid = {s: 100.0 + (sum(map(ord, s)) % 120) for s in self.symbols}  # per-symbol price
        self._peak = {}          # per-symbol peak PnL, for drawdown
        self._strats = {}
        self.lat_samples = deque(maxlen=2000)
        self.lat_sum_ns = 0            # cumulative SW compute time (for time-saved)
        self.n_signals = 0
        self.lat_series = deque(maxlen=120)   # per-second median latency (µs)
        self._sec_bucket = []
        self._sec_mark = time.time()
        self.events = deque(maxlen=40)
        self.log = deque(maxlen=60)
        self._was_halted = False
        self.lock = threading.Lock()
        # ---------------- crypto / live trading ----------------
        # Separate from the equity simulator above: its own feed, its own
        # strategies, its own broker. `trading` is the arm switch the page's
        # "Begin Trading" button flips -- quotes stream regardless, but nothing
        # is routed to the broker until it is on.
        self.crypto_symbol = "BTC/USD"
        self.trading       = False
        self.strategy_book = StrategyBook(enabled=["market_maker"], half_spread=200)
        self.bridge        = AlpacaBridge(CryptoRiskConfig(symbol=self.crypto_symbol),
                                          paper=True, dry_run=True)
        self.feed          = CryptoFeed(symbol=self.crypto_symbol,
                                        on_quote=self._on_quote, source="auto")
        self.crypto_mark   = 0.0
        self.crypto_bid    = 0
        self.crypto_ask    = 0
        self.n_intents     = 0
        self.n_routed      = 0
        self.n_rejected    = 0
        self.broker_log    = deque(maxlen=25)
        self.feed.start()

        self.event("server started (mode=%s, %d symbols)" % (self.mode, len(self.symbols)))
        self.event("crypto feed %s source=%s" % (self.crypto_symbol, self.feed.source))

    def sim_enabled(self) -> bool:
        """The equity simulator runs only when the live feed is not."""
        return not (self.trading and self.feed.status()["live"])

    # -- crypto trading ----------------------------------------------------
    def _on_quote(self, bid_t, bid_s, ask_t, ask_s):
        """One top-of-book update from the crypto feed.

        Every enabled strategy sees it, so their P&L is comparable on identical
        data. Fills are MODELLED here (dry-run): a taker crosses the spread so it
        fills at its own price; a maker only fills when the market trades through
        its resting quote. Once the bridge is armed for real, fills should come
        from the broker instead -- this model is for the dry-run P&L only.
        """
        self.crypto_bid, self.crypto_ask = bid_t, ask_t
        self.crypto_mark = (bid_t + ask_t) / 2.0 / FEED_TICKS
        if not self.trading or self.paused or not self.running:
            return
        for name, intent in self.strategy_book.on_book(bid_t, bid_s, ask_t, ask_s):
            self.n_intents += 1
            ok, why = self.bridge.send_decision_sync(
                BrDecision(action=intent.action, price_ticks=intent.price_ticks,
                           size=intent.size), rate_key=name)
            if ok:
                self.n_routed += 1
            else:
                self.n_rejected += 1
                self.broker_log.appendleft("%s  %s %s blocked: %s" % (
                    time.strftime("%H:%M:%S"), name,
                    "BUY" if intent.action == 0 else "SELL", why))
                continue

            # Fill model (dry-run only; once armed for real, fills come from the
            # broker). A taker crosses the spread, so it fills at its own price.
            # A maker rests INSIDE the spread and is therefore best bid/ask -- it
            # fills when the market comes to it, i.e. when the touch reaches our
            # price. Requiring the market to cross all the way through us was too
            # strict: the maker quotes 200 ticks inside a ~$2 BTC spread and never
            # filled once in 2103 quotes.
            filled = (not intent.passive) or (
                (intent.action == 0 and bid_t <= intent.price_ticks) or
                (intent.action == 1 and ask_t >= intent.price_ticks))
            if filled:
                # Book the ACTUAL traded quantity, not the RTL lot count: one
                # RTL lot (100 "shares") is qty_per_lot of the base asset.
                qty  = (intent.size / 100.0) * self.bridge.cfg.qty_per_lot
                fee  = (intent.price_ticks / FEED_TICKS) * qty * (self.portfolio.fee_bps / 1e4)
                self.strategy_book.on_fill(name, intent.action, qty,
                                           intent.price_ticks, fee_usd=fee)

                # Also book it into the MAIN portfolio, so the dashboard's P&L,
                # equity curve, trade blotter and per-symbol book show the real
                # BTC trading instead of the synthetic equity walk. Portfolio
                # wants integer sizes, so trade in "micro-BTC" (1e-6 BTC) units
                # -- a fractional qty would silently truncate to zero.
                micro = max(1, int(round(qty * 1_000_000)))
                self.portfolio.mark_price(self.crypto_symbol,
                                          int((bid_t + ask_t) // 2))
                self.portfolio.set_quote(self.crypto_symbol, bid_t, ask_t)
                self.portfolio.on_fill(self.crypto_symbol, intent.action, micro,
                                       intent.price_ticks, fee=fee)

    # -- helpers -----------------------------------------------------------
    def event(self, msg, level="info"):
        self.events.appendleft({"time": time.strftime("%H:%M:%S"), "msg": msg, "level": level})
        self.log.appendleft("%s %s" % (time.strftime("%H:%M:%S"), msg))

    def _strat(self, sym):
        if sym not in self._strats:
            self._strats[sym] = SoftwareStrategy(bid_thresh=self.threshold, ask_thresh=self.threshold)
        return self._strats[sym]

    def _next_quote(self, sym):
        mid = self._mid[sym] + self._rng.choice([-0.02, -0.01, 0, 0, 0.01, 0.02])
        mid = max(20.0, mid)
        self._mid[sym] = mid
        bs = self._rng.randint(80, 300)
        asz = self._rng.randint(80, 300)
        if self._rng.random() < 0.16:                 # imbalance burst
            if self._rng.random() < 0.5:
                bs = self._rng.randint(800, 2500)
            else:
                asz = self._rng.randint(800, 2500)
        bp = int(round((mid - 0.025) * TICKS_PER_USD))
        ap = int(round((mid + 0.025) * TICKS_PER_USD))
        return bp, bs, ap, asz

    def _record_lat(self, ns):
        self.lat_samples.append(ns)
        self.lat_sum_ns += ns
        self.n_signals += 1
        self._sec_bucket.append(ns)

    # -- one simulation step for one symbol --------------------------------
    def _step_symbol(self, sym):
        bp, bs, ap, asz = self._next_quote(sym)
        mid = (bp + ap) // 2
        self.portfolio.mark_price(sym, mid)
        self.portfolio.set_quote(sym, bp, ap)
        if self.portfolio.halted:
            return
        if self.mode == "taker":
            self.gauntlet.observe(sym, mid)
            t0 = time.perf_counter_ns()
            dec = self._strat(sym).evaluate(True, bp, bs, ap, asz)
            lat = time.perf_counter_ns() - t0
            if dec is None:
                return
            self._record_lat(lat)
            permitted, reason = self.gauntlet.evaluate(
                sym, dec, bp, bs, ap, asz, position=self.portfolio.books[sym].pos)
            if permitted is not None:
                self.portfolio.on_fill(sym, permitted.action, permitted.size,
                                       permitted.price, lat_ns=lat)
        else:  # maker
            inv = self.portfolio.books[sym].pos
            t0 = time.perf_counter_ns()
            q = self.mm.quote(bp, bs, ap, asz, inv)
            lat = time.perf_counter_ns() - t0
            self._record_lat(lat)
            if q.bid_size and q.bid_price and self._rng.random() < 0.25:
                self.portfolio.on_fill(sym, 0, q.bid_size, q.bid_price, lat_ns=lat)
            if q.ask_size and q.ask_price and self._rng.random() < 0.25:
                self.portfolio.on_fill(sym, 1, q.ask_size, q.ask_price, lat_ns=lat)

    def step(self):
        if not (self.running and not self.paused):
            return
        with self.lock:
            for sym in list(self.symbols):
                self._step_symbol(sym)
                b = self.portfolio.books.get(sym)
                if b:                              # track per-symbol peak PnL for drawdown
                    pnl = b.realized + b.unrealized()
                    self._peak[sym] = max(self._peak.get(sym, pnl), pnl)
            # kill-switch transition -> event
            if self.portfolio.halted and not self._was_halted:
                self.event("KILL SWITCH tripped: %s" % self.portfolio.halt_reason, "alert")
            self._was_halted = self.portfolio.halted
            # per-second latency median for the latency-over-time line
            if time.time() - self._sec_mark >= 1.0:
                if self._sec_bucket:
                    self.lat_series.append(round(statistics.median(self._sec_bucket) / 1000, 2))
                self._sec_bucket = []
                self._sec_mark = time.time()

    # -- control commands from the page ------------------------------------
    def command(self, data):
        cmd = data.get("cmd")
        with self.lock:
            if cmd == "start":
                self.running, self.paused = True, False; self.event("START")
            elif cmd == "stop":
                self.running = False; self.event("STOP")
            elif cmd == "pause":
                self.paused = True; self.event("PAUSE")
            elif cmd == "resume":
                self.paused = False; self.event("RESUME")
            elif cmd == "mode":
                self.mode = "taker" if self.mode == "maker" else "maker"
                self.event("mode -> %s" % self.mode)
            elif cmd == "feed":
                # Switch the market-data source at runtime. Going to alpaca needs
                # credentials; CryptoFeed downgrades to sim on its own if they are
                # missing, and we report what actually happened.
                want = data.get("source", "sim")
                if want not in ("sim", "alpaca"):
                    return {"ok": False, "error": "source must be sim or alpaca"}
                if want == "sim" and not self.bridge.dry_run:
                    return {"ok": False, "error":
                            "refusing to switch to the simulated feed while the "
                            "broker is armed for real orders — set broker to "
                            "DRY-RUN first"}
                self.trading = False          # always disarm across a feed change
                try:
                    self.feed.stop()
                except Exception:
                    pass
                self.feed = CryptoFeed(symbol=self.crypto_symbol,
                                       on_quote=self._on_quote, source=want)
                self.feed.start()
                self.event("feed -> %s (trading disarmed)" % self.feed.source, "warn")
                if self.feed.source != want:
                    return {"ok": True, "warning":
                            "requested %s, got %s (no alpaca credentials)"
                            % (want, self.feed.source), "source": self.feed.source}
            elif cmd == "begin_trade":
                # HARD GUARD: never send real orders priced off simulated data.
                # A simulated book with a live broker is the worst combination in
                # this whole system -- real money moving on prices that do not
                # exist -- so it is refused outright rather than warned about.
                if not self.bridge.dry_run and not self.feed.status()["live"]:
                    return {"ok": False, "error":
                            "REFUSED: broker is %s but the feed is SIMULATED. "
                            "Switch the feed to alpaca (needs APCA_API_KEY_ID / "
                            "APCA_API_SECRET_KEY) or set the broker to DRY-RUN."
                            % self.bridge.stats()["mode"]}
                # Arm routing. Quotes already stream; this is what lets intents
                # reach the broker at all.
                self.trading = True
                self.running, self.paused = True, False
                self.event("BEGIN TRADING  %s  [%s]  strategies=%s" % (
                    self.crypto_symbol, self.bridge.stats()["mode"],
                    ",".join(self.strategy_book.adapters) or "none"), "warn")
            elif cmd == "stop_trade":
                self.trading = False
                self.event("TRADING STOPPED", "warn")
            elif cmd == "strategy":
                name = data.get("name")
                if name not in STRATS_AVAILABLE:
                    return {"ok": False, "error": "unknown strategy %r" % name}
                if data.get("on"):
                    self.strategy_book.enable(name, half_spread=self.mm.cfg.half_spread_ticks,
                                              threshold=self.threshold)
                    self.event("strategy ON  %s" % name)
                else:
                    self.strategy_book.disable(name)
                    self.event("strategy OFF %s" % name)
            elif cmd == "broker":
                # dryrun -> paper -> live. Going live needs confirm=True from the
                # page AND credentials; without either it stays where it is.
                want = data.get("mode", "dryrun")
                if want == "live" and not data.get("confirm"):
                    return {"ok": False, "error": "live mode requires confirm"}
                if want in ("paper", "live") and not self.feed.status()["live"]:
                    return {"ok": False, "error":
                            "REFUSED: cannot arm %s while the feed is SIMULATED. "
                            "Switch the feed to alpaca first." % want.upper()}
                mode = self.bridge.arm(paper=(want != "live"), live=(want != "dryrun"))
                self.event("broker -> %s" % mode, "warn" if mode == "LIVE" else "info")
                if mode != want.upper() and want != "dryrun":
                    return {"ok": True, "warning": "requested %s, got %s "
                            "(missing SDK or credentials)" % (want, mode), "mode": mode}
            elif cmd == "max_position":
                try:
                    self.bridge.cfg.max_position_usd = max(1.0, float(data.get("value", 10000)))
                    self.event("max exposure -> $%.2f" % self.bridge.cfg.max_position_usd)
                except (TypeError, ValueError):
                    return {"ok": False, "error": "bad exposure"}
            elif cmd == "max_notional":
                try:
                    self.bridge.cfg.max_notional_usd = max(1.0, float(data.get("value", 100)))
                    self.event("max notional -> $%.2f" % self.bridge.cfg.max_notional_usd)
                except (TypeError, ValueError):
                    return {"ok": False, "error": "bad notional"}
            elif cmd == "reset_kill":
                self.portfolio.halted = False; self.portfolio.halt_reason = None
                self.portfolio.peak_equity = self.portfolio.equity()
                self._was_halted = False; self.event("kill switch RESET")
            elif cmd == "threshold":
                try:
                    self.threshold = max(5, min(80, int(data.get("value", 15))))
                    self._strats.clear()
                    self.event("threshold -> %d" % self.threshold)
                except (TypeError, ValueError):
                    return {"ok": False, "error": "bad threshold"}
            elif cmd == "add_symbol":
                sym = str(data.get("value", "")).strip().upper()
                if not (sym.isalnum() and 1 <= len(sym) <= 6):
                    return {"ok": False, "error": "bad symbol"}
                if sym in self.symbols:
                    return {"ok": False, "error": "already trading %s" % sym}
                self.symbols.append(sym)
                self._mid[sym] = 100.0 + (sum(map(ord, sym)) % 120)
                self.event("added symbol %s" % sym)
            elif cmd == "remove_symbol":
                sym = str(data.get("value", "")).strip().upper()
                if sym not in self.symbols:
                    return {"ok": False, "error": "not trading %s" % sym}
                if len(self.symbols) <= 1:
                    return {"ok": False, "error": "keep at least one symbol"}
                self.symbols.remove(sym)
                self.event("removed symbol %s (position frozen)" % sym)
            elif cmd == "config":
                try:
                    applied = []
                    if "half_spread" in data:
                        self.mm.cfg.half_spread_ticks = max(1, min(2000, int(data["half_spread"]))); applied.append("half_spread=%d" % self.mm.cfg.half_spread_ticks)
                    if "fee_bps" in data:
                        self.portfolio.fee_bps = max(0.0, min(50.0, float(data["fee_bps"]))); applied.append("fee=%.2fbps" % self.portfolio.fee_bps)
                    if "gross_cap" in data:
                        self.portfolio.max_gross_notional = max(1e4, float(data["gross_cap"])); applied.append("gross_cap")
                    if "daily_loss" in data:
                        self.portfolio.daily_loss_limit = max(1e3, float(data["daily_loss"])); applied.append("daily_loss")
                    self.event("config: " + ", ".join(applied) if applied else "config: (no change)")
                except (TypeError, ValueError):
                    return {"ok": False, "error": "bad config value"}
            else:
                return {"ok": False, "error": "unknown cmd"}
        return {"ok": True}

    # -- state snapshot for the page ---------------------------------------
    def state(self):
        pf = self.portfolio
        with self.lock:
            lat = list(self.lat_samples)
            trades = list(pf.trades)
        def pct(p):
            return int(sorted(lat)[min(len(lat) - 1, int(p * len(lat)))]) if lat else 0
        return {
            "updated": time.strftime("%H:%M:%S"),
            "status": {"mode": self.mode, "symbols": self.symbols, "execute": False,
                       "source": ("ALPACA LIVE" if not self.sim_enabled() else "SIM"),
                       "uptime_s": round(time.time() - self.started, 1),
                       "halted": pf.halted, "halt_reason": pf.halt_reason,
                       "running": self.running, "paused": self.paused,
                       "threshold": self.threshold,
                       "config": {"half_spread": self.mm.cfg.half_spread_ticks,
                                  "fee_bps": self.portfolio.fee_bps,
                                  "gross_cap": self.portfolio.max_gross_notional,
                                  "daily_loss": self.portfolio.daily_loss_limit}},
            "pnl": {"realized": round(pf.realized(), 2), "unrealized": round(pf.unrealized(), 2),
                    "equity": round(pf.equity(), 2), "net_exposure": round(pf.net_exposure(), 0),
                    "gross_exposure": round(pf.gross_exposure(), 0), "gross_cap": pf.max_gross_notional},
            "books": [{"symbol": s, "pos": b.pos, "avg": round(b.avg_cost, 2),
                       "mark": round(b.mark, 2), "realized": round(b.realized, 2),
                       "unrealized": round(b.unrealized(), 2),
                       "bid": round(b.bid, 2), "ask": round(b.ask, 2),
                       "drawdown": round(max(0.0, self._peak.get(s, 0.0) - (b.realized + b.unrealized())), 2),
                       "active": s in self.symbols}
                      for s, b in sorted(pf.books.items())],
            "stats": pf.trade_stats(),
            "orders": {"fill": len(trades)},     # sim: every recorded trade is a fill
            "trades": [{"time": time.strftime("%H:%M:%S", time.localtime(t["t"])),
                        "symbol": t["symbol"], "side": t["side"], "size": t["size"],
                        "price": t["price"], "lat_ns": t.get("lat_ns"), "pnl": t.get("pnl"),
                        "slip": t.get("slip")}
                       for t in trades[-25:][::-1]],
            "n_trades": len(trades),
            "funnel": dict(self.gauntlet.stats),
            "latency": {"n": self.n_signals, "sum_ns": self.lat_sum_ns, "fpga_ns": FPGA_NS,
                        "median_ns": int(statistics.median(lat)) if lat else 0,
                        "p50_ns": pct(0.50), "p90_ns": pct(0.90), "p99_ns": pct(0.99),
                        "max_ns": max(lat) if lat else 0, "net_ms": 0,
                        "samples": lat[-400:], "series": list(self.lat_series)},
            "equity_curve": pf.equity_curve[-200:],
            "realized_curve": pf.realized_curve[-200:],
            "events": list(self.events),
            "log": list(self.log),
            # ---------------- crypto / live trading ----------------
            "crypto": dict(self.feed.status(), trading=self.trading,
                           intents=self.n_intents, routed=self.n_routed,
                           rejected=self.n_rejected,
                           # True only when real orders would ride on real prices
                           safe_to_arm=(self.bridge.dry_run
                                        or self.feed.status()["live"])),
            "broker": dict(self.bridge.stats(),
                           max_notional=self.bridge.cfg.max_notional_usd,
                           max_position=self.bridge.cfg.max_position_usd,
                           log=list(self.broker_log)),
            "strategies": {
                "available": STRATS_AVAILABLE,
                "enabled":   list(self.strategy_book.adapters),
                "rows":      self.strategy_book.stats(self.crypto_mark),
                "totals":    self.strategy_book.totals(self.crypto_mark),
            },
        }


def _sim_loop(ctrl):
    """Synthetic equity ticks. Stands down entirely while the live crypto feed
    is driving the dashboard -- otherwise the panels blend fake MAG7 activity
    with real BTC trading and none of the numbers mean anything."""
    period = 1.0 / ctrl.rate
    while True:
        if not ctrl.sim_enabled():
            time.sleep(0.5)
            continue
        ctrl.step()
        time.sleep(period)


def _writer_loop(ctrl):
    path = os.path.join(REPORTS, "run_state.json")
    tmp = path + ".tmp"
    while True:
        try:
            # write to a temp file then atomically rename, so a reader (the
            # browser) never sees a half-written / empty file.
            with open(tmp, "w") as fh:
                json.dump(ctrl.state(), fh)
            os.replace(tmp, path)
        except Exception:
            pass
        time.sleep(0.5)


class Handler(SimpleHTTPRequestHandler):
    ctrl = None
    def __init__(self, *a, **k):
        super().__init__(*a, directory=REPORTS, **k)
    def log_message(self, *a):
        pass                                    # quiet
    def do_GET(self):
        if self.path == "/":
            self.path = "/live.html"
        if self.path.split("?")[0] == "/trades.csv":
            return self._send_csv()
        return super().do_GET()
    def _send_csv(self):
        rows = ["time,symbol,side,size,price,pnl,fee,lat_ns"]
        for t in list(self.ctrl.portfolio.trades):
            rows.append("%s,%s,%s,%d,%.4f,%s,%s,%s" % (
                time.strftime("%H:%M:%S", time.localtime(t["t"])), t["symbol"], t["side"],
                t["size"], t["price"], t.get("pnl", ""), t.get("fee", ""), t.get("lat_ns", "")))
        body = ("\n".join(rows)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Disposition", "attachment; filename=trades.csv")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        if self.path.split("?")[0] != "/control":
            self.send_error(404); return
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
            res = self.ctrl.command(data)
        except Exception as e:
            res = {"ok": False, "error": str(e)}
        body = json.dumps(res).encode()
        self.send_response(200 if res.get("ok") else 400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser(description="live monitor control server")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--rate", type=int, default=20, help="sim ticks/sec")
    args = ap.parse_args()

    ctrl = Controller(rate=args.rate)
    threading.Thread(target=_sim_loop, args=(ctrl,), daemon=True).start()
    threading.Thread(target=_writer_loop, args=(ctrl,), daemon=True).start()

    Handler.ctrl = ctrl
    httpd = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("live control monitor: http://localhost:%d/live.html" % args.port)
    print("  controllable SIM (start/stop/pause, taker<->maker, threshold, reset kill)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")


if __name__ == "__main__":
    main()
