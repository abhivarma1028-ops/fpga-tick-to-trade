"""
Portfolio — cross-symbol position, PnL and exposure tracking.

This is the SURVIVAL layer of the trade brain. Where strategy_sw.py decides
"should I trade THIS symbol right now?", the Portfolio answers the firm-wide
questions a real HFT desk asks continuously:

  * How much am I holding, per symbol and in total?         (position)
  * How much have I made / lost so far?                      (realized + unrealized PnL)
  * How much market risk am I carrying right now?            (net / gross exposure)
  * Have I lost too much today — should I STOP?              (daily-loss kill switch)

One Portfolio instance is shared by every per-symbol LiveFeed, so limits and the
kill switch are enforced across the whole book, not one symbol at a time.

Money convention: prices arrive as ITCH fixed-point ticks (USD * 10_000). This
module works in plain USD floats internally (it is a software risk overlay, not
the latency-critical signal path, so bit-exactness is not required here).
"""

import time
from dataclasses import dataclass, field

TICKS_PER_USD = 10_000


def ticks_to_usd(ticks: int) -> float:
    return ticks / TICKS_PER_USD


@dataclass
class SymbolBook:
    """Per-symbol running state."""
    pos: int = 0            # signed shares: + long, - short
    avg_cost: float = 0.0   # average entry price (USD) of the OPEN position
    realized: float = 0.0   # realized PnL in USD (booked on reducing trades)
    mark: float = 0.0       # last mid price (USD), for unrealized PnL / exposure
    bid: float = 0.0        # last top-of-book bid (USD) — for the live market view
    ask: float = 0.0        # last top-of-book ask (USD)

    def unrealized(self) -> float:
        # long profits when mark > cost; short profits when mark < cost.
        return self.pos * (self.mark - self.avg_cost)

    def notional(self) -> float:
        return self.pos * self.mark


class Portfolio:
    def __init__(self, max_gross_notional_usd: float = 2_000_000.0,
                 daily_loss_limit_usd: float = 10_000.0, fee_bps: float = 1.0):
        self.books: dict[str, SymbolBook] = {}
        self.max_gross_notional = max_gross_notional_usd
        self.daily_loss_limit = daily_loss_limit_usd
        self.fee_bps = fee_bps        # per-fill fee, basis points of notional
        self.fees = 0.0               # cumulative fees paid (USD)
        self.halted = False          # kill switch tripped -> stop trading everywhere
        self.halt_reason = None
        self.peak_equity = 0.0        # high-water mark, for drawdown
        self.equity_curve = []        # equity (realized+unreal) after each fill
        self.realized_curve = []      # realized-only PnL after each fill
        self.trades = []              # every fill, for the live monitor

    def _book(self, symbol: str) -> SymbolBook:
        return self.books.setdefault(symbol, SymbolBook())

    # -- market data -------------------------------------------------------
    def mark_price(self, symbol: str, mid_ticks: int):
        """Update the last mid for a symbol (drives unrealized PnL + exposure)."""
        b = self._book(symbol)
        b.mark = ticks_to_usd(mid_ticks)
        if b.pos == 0 and b.avg_cost == 0.0:
            b.avg_cost = b.mark   # seed so an unmarked flat book reads 0 PnL cleanly

    def set_quote(self, symbol: str, bid_ticks: int, ask_ticks: int):
        """Record the current top-of-book for the live market view."""
        b = self._book(symbol)
        b.bid = ticks_to_usd(bid_ticks)
        b.ask = ticks_to_usd(ask_ticks)

    def set_position(self, symbol: str, pos: int, avg_cost_usd: float,
                     mark_usd: float = None):
        """Reconcile an open position to the BROKER's truth (real qty + avg entry).

        Aligns the book without booking a trade or PnL event — used at startup and
        periodically so equity / exposure / drawdown reflect the actual account,
        even across restarts or manual trades outside this process. Realized PnL to
        date is left untouched (it is a historical fact of this session)."""
        b = self._book(symbol)
        b.pos = int(pos)
        b.avg_cost = float(avg_cost_usd) if pos else 0.0
        if mark_usd is not None:
            b.mark = float(mark_usd)
        elif b.mark == 0.0 and avg_cost_usd:
            b.mark = float(avg_cost_usd)

    # -- fills -------------------------------------------------------------
    def on_fill(self, symbol: str, action: int, size: int, price_ticks: int,
                lat_ns: int = None, fee: float = None, req_price_ticks: int = None):
        """Record a fill: action 0 = BUY (+size), 1 = SELL (-size).

        `lat_ns` is the software compute time for THIS decision (tick->signal or
        quote), attached to the trade so the live monitor can show per-trade speed.

        `fee` is the REAL broker fee/commission for this fill when known (live
        execute path). When None, the modelled fee (fee_bps * notional) is used —
        that is the sim / dry-run case.

        Realized PnL is booked when a fill REDUCES the open position (the
        classic average-cost accounting used on a trading book).
        """
        b = self._book(symbol)
        price = ticks_to_usd(price_ticks)
        qty = size if action == 0 else -size      # signed change
        realized_before = b.realized

        if b.pos == 0 or (b.pos > 0) == (qty > 0):
            # opening or adding in the same direction -> blend the average cost
            new_pos = b.pos + qty
            b.avg_cost = (b.avg_cost * abs(b.pos) + price * abs(qty)) / abs(new_pos)
            b.pos = new_pos
        else:
            # reducing / closing (qty is opposite sign to the position)
            closing = min(abs(qty), abs(b.pos))
            sign = 1 if b.pos > 0 else -1
            b.realized += (price - b.avg_cost) * closing * sign
            b.pos += qty
            if b.pos == 0:
                b.avg_cost = 0.0
            elif (b.pos > 0) != (sign > 0):
                # flipped through zero to the other side -> new position at fill price
                b.avg_cost = price

        if fee is None:                       # sim / dry-run: model the fee
            fee = (self.fee_bps / 1e4) * price * size
        self.fees += fee
        # slippage = adverse move from the requested/limit price to the actual fill,
        # in $ (positive = paid worse than intended). BUY worse when fill>req; SELL
        # worse when fill<req. Real only on the live execute path; 0 in sim.
        slip = None
        if req_price_ticks is not None:
            req = ticks_to_usd(req_price_ticks)
            slip = ((price - req) if action == 0 else (req - price)) * size
        self._check_kill()
        self.equity_curve.append(self.equity())
        self.realized_curve.append(round(self.realized(), 2))
        self.trades.append({
            "t": time.time(), "symbol": symbol,
            "side": "BUY" if action == 0 else "SELL",
            "size": size, "price": round(price, 4),
            "lat_ns": int(lat_ns) if lat_ns is not None else None,
            "pnl": round(b.realized - realized_before, 2),   # realized on THIS fill
            "fee": round(fee, 4),
            "slip": round(slip, 4) if slip is not None else None,
        })

    # -- aggregate views ---------------------------------------------------
    def realized(self) -> float:
        return sum(b.realized for b in self.books.values())

    def unrealized(self) -> float:
        return sum(b.unrealized() for b in self.books.values())

    def equity(self) -> float:
        """Total PnL = realized + open (unrealized)."""
        return self.realized() + self.unrealized()

    def net_exposure(self) -> float:
        """Signed dollar exposure (long minus short). Directional risk."""
        return sum(b.notional() for b in self.books.values())

    def gross_exposure(self) -> float:
        """Absolute dollar exposure (long plus short). Total capital at work."""
        return sum(abs(b.notional()) for b in self.books.values())

    # -- limits ------------------------------------------------------------
    def gross_room(self, symbol: str, action: int, price_ticks: int) -> int:
        """Shares still addable in `action` before max gross notional is hit.

        Trades that REDUCE gross exposure (toward flat) are never limited.
        Returns a share count (>=0); 0 means no room to add.
        """
        b = self._book(symbol)
        price = ticks_to_usd(price_ticks)
        if price <= 0:
            return 0
        qty_dir = 1 if action == 0 else -1
        # adding to the same-signed position increases gross; the opposite reduces it
        adding = (b.pos >= 0 and qty_dir > 0) or (b.pos <= 0 and qty_dir < 0)
        if not adding:
            return 10 ** 9          # reducing exposure: effectively unlimited
        headroom = self.max_gross_notional - self.gross_exposure()
        if headroom <= 0:
            return 0
        return int(headroom // price)

    def _check_kill(self):
        """Trip the kill switch on a daily loss OR a large drawdown from the peak."""
        eq = self.equity()
        self.peak_equity = max(self.peak_equity, eq)
        drawdown = self.peak_equity - eq
        if eq <= -self.daily_loss_limit:
            self.halted, self.halt_reason = True, (
                f"daily loss limit hit: equity {eq:,.0f} <= -{self.daily_loss_limit:,.0f}")
        elif drawdown >= self.daily_loss_limit:
            self.halted, self.halt_reason = True, (
                f"drawdown limit hit: {drawdown:,.0f} from peak {self.peak_equity:,.0f}")

    # -- stats (shared by the live feed and the sim monitor) ---------------
    def trade_stats(self) -> dict:
        """All trade-derived metrics, computed from the real fill history so both
        backends report identically. Every value comes from actual trades."""
        import statistics
        tr = self.trades
        closes = [t["pnl"] for t in tr if t.get("pnl")]          # realizing fills
        wins = [p for p in closes if p > 0]
        losses = [p for p in closes if p < 0]
        slips = [t["slip"] for t in tr if t.get("slip") is not None]
        vol_sh = sum(t["size"] for t in tr)
        vol_notional = sum(t["size"] * t["price"] for t in tr)
        buy_ct = sum(1 for t in tr if t["side"] == "BUY")
        sell_ct = sum(1 for t in tr if t["side"] == "SELL")
        buy_shares = sum(t["size"] for t in tr if t["side"] == "BUY")
        sell_shares = sum(t["size"] for t in tr if t["side"] == "SELL")
        # current consecutive win/loss streak (+ = wins, - = losses)
        streak = 0
        for p in reversed(closes):
            if p > 0:
                if streak < 0:
                    break
                streak += 1
            elif p < 0:
                if streak > 0:
                    break
                streak -= 1
        # portfolio max drawdown + current drawdown duration (fills since equity high)
        peak = None
        maxdd = 0.0
        peak_i = 0
        for i, e in enumerate(self.equity_curve):
            if peak is None or e >= peak:
                peak, peak_i = e, i
            maxdd = max(maxdd, peak - e)
        dd_dur = (len(self.equity_curve) - 1 - peak_i) if self.equity_curve else 0
        # best / worst symbol by total PnL
        best = worst = None
        if self.books:
            r = sorted(self.books.items(),
                       key=lambda kv: kv[1].realized + kv[1].unrealized())
            worst = {"symbol": r[0][0], "pnl": round(r[0][1].realized + r[0][1].unrealized(), 2)}
            best = {"symbol": r[-1][0], "pnl": round(r[-1][1].realized + r[-1][1].unrealized(), 2)}
        eq = self.equity()
        return {
            "wins": len(wins), "losses": len(losses),
            "win_rate": round(100 * len(wins) / (len(wins) + len(losses)), 1) if closes else 0.0,
            "avg_win": round(statistics.fmean(wins), 2) if wins else 0.0,
            "avg_loss": round(statistics.fmean(losses), 2) if losses else 0.0,
            "fees": round(self.fees, 2),
            "equity_net": round(eq - self.fees, 2),
            "best": best, "worst": worst,
            "volume_shares": vol_sh,
            "volume_notional": round(vol_notional, 0),
            "buy_ct": buy_ct, "sell_ct": sell_ct,
            "buy_shares": buy_shares, "sell_shares": sell_shares,
            "biggest_win": round(max(wins), 2) if wins else 0.0,
            "biggest_loss": round(min(losses), 2) if losses else 0.0,
            "streak": streak,
            "max_dd": round(maxdd, 2), "dd_dur": dd_dur,
            "ret_pct": round(100 * eq / self.max_gross_notional, 3) if self.max_gross_notional else 0.0,
            "avg_slip": round(statistics.fmean(slips), 4) if slips else 0.0,
            "n_slip": len(slips),
        }

    # -- reporting ---------------------------------------------------------
    def summary_lines(self) -> list[str]:
        lines = ["==== PORTFOLIO SUMMARY ===="]
        lines.append(f"{'symbol':<8}{'pos':>8}{'avg':>10}{'mark':>10}"
                     f"{'realized':>12}{'unreal':>12}")
        for sym, b in sorted(self.books.items()):
            lines.append(f"{sym:<8}{b.pos:>8}{b.avg_cost:>10.2f}{b.mark:>10.2f}"
                         f"{b.realized:>12.2f}{b.unrealized():>12.2f}")
        lines.append(f"{'TOTAL':<8}{'':>8}{'':>10}{'':>10}"
                     f"{self.realized():>12.2f}{self.unrealized():>12.2f}")
        lines.append(f"equity (realized+unreal) : ${self.equity():,.2f}")
        lines.append(f"net exposure             : ${self.net_exposure():,.0f}")
        lines.append(f"gross exposure           : ${self.gross_exposure():,.0f} "
                     f"(cap ${self.max_gross_notional:,.0f})")
        lines.append(f"kill switch              : {'TRIPPED — ' + self.halt_reason if self.halted else 'ok'}")
        return lines
