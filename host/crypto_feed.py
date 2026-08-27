"""
crypto_feed — live BTC/USD top-of-book, with a simulated fallback.

Replaces monitor_server's `_sim_loop` random walk over equities with a real
crypto feed, while still working with no credentials so the dashboard is usable
before any keys exist.

    feed = CryptoFeed(symbol="BTC/USD", on_quote=cb)
    feed.start()          # background thread; cb(bid_ticks, bid_sz, ask_ticks, ask_sz)

SOURCE SELECTION
    alpaca  live Alpaca CryptoDataStream (needs APCA_API_KEY_ID / _SECRET_KEY)
    sim     seeded random walk around a plausible BTC price
    auto    alpaca if the SDK and credentials are both present, else sim

UNITS
Prices are ITCH fixed-point ticks (USD * 10_000), matching the RTL, Portfolio and
every strategy. Crypto quote sizes are fractional BTC, but the strategies expect
integer share-like counts, so sizes are scaled by SIZE_SCALE -- 0.75 BTC becomes
7500. That keeps the imbalance/OFI arithmetic meaningful instead of collapsing
every size to 0 or 1.
"""
from __future__ import annotations

import os
import time
import random
import logging
import threading

log = logging.getLogger(__name__)

TICKS_PER_USD = 10_000
SIZE_SCALE    = 10_000        # fractional BTC -> integer "shares"

try:
    from alpaca.data.live import CryptoDataStream
    ALPACA_AVAILABLE = True
except ImportError:                                   # pragma: no cover
    ALPACA_AVAILABLE = False


def credentials() -> tuple[str | None, str | None]:
    return (os.environ.get("APCA_API_KEY_ID"),
            os.environ.get("APCA_API_SECRET_KEY"))


def can_use_alpaca() -> bool:
    k, s = credentials()
    return bool(ALPACA_AVAILABLE and k and s)


class CryptoFeed:
    def __init__(self, symbol: str = "BTC/USD", on_quote=None,
                 source: str = "auto", rate: int = 5, seed: int = 1):
        self.symbol   = symbol
        self.on_quote = on_quote or (lambda *a: None)
        self.rate     = max(1, rate)          # sim ticks/sec
        self._rng     = random.Random(seed)
        self._stop    = threading.Event()
        self._thread  = None
        self._stream  = None

        if source == "auto":
            source = "alpaca" if can_use_alpaca() else "sim"
        if source == "alpaca" and not can_use_alpaca():
            why = ("alpaca-py not installed" if not ALPACA_AVAILABLE
                   else "APCA_API_KEY_ID / APCA_API_SECRET_KEY not set")
            log.warning("alpaca feed requested but unavailable (%s) — using SIM", why)
            source = "sim"
        self.source = source

        # Sim state: start near a plausible BTC level. The absolute level does
        # not matter to the strategies (they work on imbalance and spread), but a
        # realistic one keeps the dashboard's numbers readable.
        self._mid    = 60_000.0
        self.n_ticks = 0
        self.last    = None                   # (bid_t, bid_s, ask_t, ask_s)

    # ------------------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        target = self._run_alpaca if self.source == "alpaca" else self._run_sim
        self._thread = threading.Thread(target=target, daemon=True,
                                        name=f"feed-{self.source}")
        self._thread.start()
        log.info("crypto feed started: %s source=%s", self.symbol, self.source)

    def stop(self):
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _emit(self, bid_usd, bid_qty, ask_usd, ask_qty):
        bt = int(round(bid_usd * TICKS_PER_USD))
        at = int(round(ask_usd * TICKS_PER_USD))
        bs = max(1, int(round(bid_qty * SIZE_SCALE)))
        asz = max(1, int(round(ask_qty * SIZE_SCALE)))
        if at <= bt:                          # never hand the book a crossed quote
            at = bt + 1
        self.n_ticks += 1
        self.last = (bt, bs, at, asz)
        try:
            self.on_quote(bt, bs, at, asz)
        except Exception:
            log.exception("on_quote callback raised")

    # ------------------------------------------------------------------
    def _run_sim(self):
        """Random walk with a varying spread and lopsided sizes, so the
        imbalance/OFI strategies actually see something to react to."""
        period = 1.0 / self.rate
        while not self._stop.is_set():
            self._mid *= (1.0 + self._rng.gauss(0, 0.00035))
            half = self._mid * self._rng.uniform(0.00002, 0.00012)
            bq = self._rng.uniform(0.05, 2.5)
            aq = self._rng.uniform(0.05, 2.5)
            self._emit(self._mid - half, bq, self._mid + half, aq)
            time.sleep(period)

    def _run_alpaca(self):
        key, secret = credentials()
        self._stream = CryptoDataStream(key, secret)

        async def handler(q):
            # Alpaca quote: bid_price / bid_size / ask_price / ask_size
            if q.bid_price and q.ask_price:
                self._emit(float(q.bid_price), float(q.bid_size or 0.01),
                           float(q.ask_price), float(q.ask_size or 0.01))

        self._stream.subscribe_quotes(handler, self.symbol)
        while not self._stop.is_set():
            try:
                self._stream.run()            # blocks; returns on stop/error
            except Exception as e:
                log.error("alpaca stream error: %s — retrying in 5s", e)
                if self._stop.wait(5):
                    break

    # ------------------------------------------------------------------
    def status(self) -> dict:
        bt, bs, at, asz = self.last or (0, 0, 0, 0)
        return {
            "symbol":  self.symbol,
            "source":  self.source.upper(),
            "live":    self.source == "alpaca",
            "ticks":   self.n_ticks,
            "bid":     round(bt / TICKS_PER_USD, 2),
            "ask":     round(at / TICKS_PER_USD, 2),
            "bid_qty": round(bs / SIZE_SCALE, 4),
            "ask_qty": round(asz / SIZE_SCALE, 4),
            "mid":     round((bt + at) / 2 / TICKS_PER_USD, 2),
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    seen = []
    f = CryptoFeed(on_quote=lambda *a: seen.append(a))
    f.start()
    time.sleep(3)
    f.stop()
    print(f"source={f.source}  ticks={f.n_ticks}")
    print("status:", f.status())
    for q in seen[:3]:
        print(f"  bid {q[0]/TICKS_PER_USD:,.2f} x{q[1]/SIZE_SCALE:.4f}   "
              f"ask {q[2]/TICKS_PER_USD:,.2f} x{q[3]/SIZE_SCALE:.4f}")
