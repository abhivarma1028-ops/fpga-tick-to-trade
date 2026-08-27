"""
strategy_registry — one interface over every strategy, with per-strategy P&L.

Two problems this solves.

1. THE STRATEGIES HAVE TWO DIFFERENT SHAPES.
   Makers  (MarketMaker, AvellanedaStoikov) expose
       quote(bid_p, bid_s, ask_p, ask_s, inventory) -> Quote(bid/ask price+size)
   Takers  (SoftwareStrategy, OFISignal) expose
       update(bid_p, bid_s, ask_p, ask_s, ...) -> Decision | None
   The control server and the web page should not care which is which, so both
   are normalised to `on_book(...) -> list[Intent]`.

2. YOU CANNOT SEE WHICH STRATEGY MADE THE MONEY.
   Portfolio tracks one blended P&L for the whole account. Running four
   strategies against that tells you nothing about which is worth keeping. Each
   adapter therefore keeps its OWN book -- inventory, average cost, realised
   P&L, fees -- using the same average-cost convention as Portfolio, so the legs
   sum to the whole and the page can show a per-strategy breakdown.

Prices are ITCH fixed-point ticks throughout (ticks / 10_000 = USD), matching the
RTL and Portfolio. Sizes are the RTL's integer "share" units; the broker bridge
converts those to a base-asset quantity for crypto.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from market_maker      import MarketMaker, MMConfig
from strategy_sw       import SoftwareStrategy

try:
    from avellaneda_stoikov import AvellanedaStoikov, ASConfig
    AS_AVAILABLE = True
except ImportError:                                   # pragma: no cover
    AS_AVAILABLE = False

try:
    from ofi_signal import OFISignal
    OFI_AVAILABLE = True
except ImportError:                                   # pragma: no cover
    OFI_AVAILABLE = False

log = logging.getLogger(__name__)

TICKS_PER_USD = 10_000.0


@dataclass
class Intent:
    """One normalised order intent, whatever produced it."""
    action:      int          # 0 = BUY, 1 = SELL
    price_ticks: int
    size:        int
    passive:     bool = False  # True = resting quote, False = aggressive take


@dataclass
class Leg:
    """Per-strategy book. Average-cost, matching Portfolio's convention so the
    legs add up to the account total instead of drifting away from it."""
    name:         str
    kind:         str
    decisions:    int   = 0
    fills:        int   = 0
    # Signed traded quantity in the INSTRUMENT'S OWN units -- shares for
    # equities, base asset (e.g. BTC) for crypto. The caller converts before
    # calling on_fill; booking raw RTL "share" counts against a crypto price
    # inflates P&L by ~1e6 (900 lots x $60,000 instead of 0.0009 BTC x $60,000).
    inventory:    float = 0.0
    avg_cost_usd: float = 0.0
    realized_usd: float = 0.0
    fees_usd:     float = 0.0
    last_px_usd:  float = 0.0

    def on_fill(self, action: int, size: float, price_ticks: int, fee_usd: float = 0.0):
        px = price_ticks / TICKS_PER_USD
        self.fills += 1
        self.fees_usd += fee_usd
        self.last_px_usd = px
        signed = size if action == 0 else -size

        # Closing against an opposite position realises P&L; the rest re-averages.
        if self.inventory != 0 and (self.inventory > 0) != (signed > 0):
            closed = min(abs(signed), abs(self.inventory))
            if self.inventory > 0:                       # closing a long
                self.realized_usd += (px - self.avg_cost_usd) * closed
            else:                                        # closing a short
                self.realized_usd += (self.avg_cost_usd - px) * closed
            remaining = abs(signed) - closed
            self.inventory += signed
            if remaining > 0:                            # flipped through zero
                self.avg_cost_usd = px
            elif self.inventory == 0:
                self.avg_cost_usd = 0.0
        else:
            total = abs(self.inventory) + abs(signed)
            if total:
                self.avg_cost_usd = (self.avg_cost_usd * abs(self.inventory)
                                     + px * abs(signed)) / total
            self.inventory += signed

    def unrealized_usd(self, mark_usd: float) -> float:
        if not self.inventory or not mark_usd:
            return 0.0
        if self.inventory > 0:
            return (mark_usd - self.avg_cost_usd) * self.inventory
        return (self.avg_cost_usd - mark_usd) * abs(self.inventory)

    def stats(self, mark_usd: float = 0.0) -> dict:
        unreal = self.unrealized_usd(mark_usd)
        return {
            "name":       self.name,
            "kind":       self.kind,
            "decisions":  self.decisions,
            "fills":      self.fills,
            "inventory":  round(self.inventory, 8),
            "avg_cost":   round(self.avg_cost_usd, 2),
            "realized":   round(self.realized_usd, 2),
            "unrealized": round(unreal, 2),
            "fees":       round(self.fees_usd, 4),
            # net is what actually matters: gross P&L minus what it cost to trade
            "net":        round(self.realized_usd + unreal - self.fees_usd, 2),
        }


class Adapter:
    """Base: wraps one strategy and owns its Leg."""
    kind = "taker"

    def __init__(self, name: str):
        self.name = name
        self.leg  = Leg(name=name, kind=self.kind)

    def on_book(self, bid_p, bid_s, ask_p, ask_s, inventory=0) -> list[Intent]:
        raise NotImplementedError

    def on_fill(self, action, size, price_ticks, fee_usd=0.0):
        self.leg.on_fill(action, size, price_ticks, fee_usd)

    def stats(self, mark_usd=0.0) -> dict:
        return self.leg.stats(mark_usd)


class TakerAdapter(Adapter):
    """The takers do NOT share a signature, so each factory supplies a small
    call adapter rather than the registry guessing a method name:
        SoftwareStrategy.evaluate(book_valid, bid_p, bid_s, ask_p, ask_s, ...)
        OFISignal.update(bid_p, bid_s, ask_p, ask_s, book_valid=True)
    Note book_valid is FIRST on one and a trailing kwarg on the other.
    """
    kind = "taker"

    def __init__(self, name, strat, call):
        super().__init__(name)
        self.strat = strat
        self.call  = call        # fn(bid_p, bid_s, ask_p, ask_s) -> Decision | None

    def on_book(self, bid_p, bid_s, ask_p, ask_s, inventory=0) -> list[Intent]:
        dec = self.call(bid_p, bid_s, ask_p, ask_s)
        if not dec:
            return []
        self.leg.decisions += 1
        return [Intent(action=dec.action, price_ticks=dec.price,
                       size=dec.size, passive=False)]


class MakerAdapter(Adapter):
    kind = "maker"

    def __init__(self, name, strat):
        super().__init__(name)
        self.strat = strat

    def on_book(self, bid_p, bid_s, ask_p, ask_s, inventory=0) -> list[Intent]:
        q = self.strat.quote(bid_p, bid_s, ask_p, ask_s, inventory)
        out = []
        # price 0 means "not quoting this side" -- e.g. inventory skew has pulled
        # the maker off one side entirely.
        if q.bid_price and q.bid_size:
            out.append(Intent(0, q.bid_price, q.bid_size, passive=True))
        if q.ask_price and q.ask_size:
            out.append(Intent(1, q.ask_price, q.ask_size, passive=True))
        if out:
            self.leg.decisions += 1
        return out


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
def _mk_imbalance(**kw):
    s = SoftwareStrategy(bid_thresh=kw.get("threshold", 15),
                         ask_thresh=kw.get("threshold", 15))
    return TakerAdapter("imbalance", s,
                        lambda bp, bs, ap, asz: s.evaluate(True, bp, bs, ap, asz))

def _mk_ofi(**kw):
    s = OFISignal(window=kw.get("window", 10),
                  threshold=kw.get("threshold", 1500))
    return TakerAdapter("ofi", s,
                        lambda bp, bs, ap, asz: s.update(bp, bs, ap, asz))

def _mk_market_maker(**kw):
    return MakerAdapter("market_maker", MarketMaker(
        MMConfig(half_spread_ticks=kw.get("half_spread", 200))))

def _mk_avellaneda(**kw):
    return MakerAdapter("avellaneda", AvellanedaStoikov(ASConfig()))


FACTORIES = {"imbalance": _mk_imbalance, "market_maker": _mk_market_maker}
if OFI_AVAILABLE:
    FACTORIES["ofi"] = _mk_ofi
if AS_AVAILABLE:
    FACTORIES["avellaneda"] = _mk_avellaneda

AVAILABLE = sorted(FACTORIES)


class StrategyBook:
    """Runs a set of strategies side by side and keeps their P&L apart.

    Every enabled strategy sees every book update, so their P&L is directly
    comparable on identical data -- which is the only way to tell which is
    actually worth running.
    """

    def __init__(self, enabled=None, **kw):
        self.adapters: dict[str, Adapter] = {}
        for name in (enabled or ["imbalance"]):
            self.enable(name, **kw)

    def enable(self, name: str, **kw):
        if name not in FACTORIES:
            raise ValueError(f"unknown strategy {name!r}; have {AVAILABLE}")
        if name not in self.adapters:
            self.adapters[name] = FACTORIES[name](**kw)
            log.info("strategy enabled: %s (%s)", name, self.adapters[name].kind)
        return self.adapters[name]

    def disable(self, name: str):
        # The Leg is dropped with the adapter, so its P&L disappears from the
        # breakdown. Re-enabling starts that strategy's book from flat.
        self.adapters.pop(name, None)
        log.info("strategy disabled: %s", name)

    def on_book(self, bid_p, bid_s, ask_p, ask_s) -> list[tuple[str, Intent]]:
        out = []
        for name, ad in self.adapters.items():
            for intent in ad.on_book(bid_p, bid_s, ask_p, ask_s, ad.leg.inventory):
                out.append((name, intent))
        return out

    def on_fill(self, name, action, size, price_ticks, fee_usd=0.0):
        ad = self.adapters.get(name)
        if ad:
            ad.on_fill(action, size, price_ticks, fee_usd)

    def stats(self, mark_usd=0.0) -> list[dict]:
        rows = [ad.stats(mark_usd) for ad in self.adapters.values()]
        rows.sort(key=lambda r: r["net"], reverse=True)
        return rows

    def totals(self, mark_usd=0.0) -> dict:
        rows = self.stats(mark_usd)
        return {
            "strategies": len(rows),
            "decisions":  sum(r["decisions"] for r in rows),
            "fills":      sum(r["fills"] for r in rows),
            "realized":   round(sum(r["realized"] for r in rows), 2),
            "unrealized": round(sum(r["unrealized"] for r in rows), 2),
            "fees":       round(sum(r["fees"] for r in rows), 4),
            "net":        round(sum(r["net"] for r in rows), 2),
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    print("available strategies:", AVAILABLE)

    book = StrategyBook(enabled=AVAILABLE)
    mid = 60_000.0
    for i in range(200):
        mid += (1 if i % 3 else -2) * 5.0
        bp, ap = int((mid - 5) * TICKS_PER_USD), int((mid + 5) * TICKS_PER_USD)
        for name, intent in book.on_book(bp, 500 + i % 400, ap, 300 + i % 250):
            # model an immediate fill at the intent price
            book.on_fill(name, intent.action, intent.size, intent.price_ticks,
                         fee_usd=(intent.price_ticks / TICKS_PER_USD) * intent.size * 1e-4)

    print(f"\nper-strategy P&L (mark ${mid:,.2f}):")
    print(f"  {'strategy':14} {'kind':6} {'dec':>5} {'fill':>5} {'inv':>7} "
          f"{'realized':>10} {'unreal':>10} {'fees':>8} {'net':>10}")
    for r in book.stats(mid):
        print(f"  {r['name']:14} {r['kind']:6} {r['decisions']:5} {r['fills']:5} "
              f"{r['inventory']:7} {r['realized']:10.2f} {r['unrealized']:10.2f} "
              f"{r['fees']:8.2f} {r['net']:10.2f}")
    print("\ntotals:", book.totals(mid))
