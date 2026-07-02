"""
Pre-trade gauntlet — the decision "brain" that sits between the raw signal and
an actual order, modelling the checks a real HFT desk runs on every trade.

The FPGA-equivalent strategy (strategy_sw.py) produces a *raw* signal from book
imbalance. That signal is necessary but not sufficient — a real firm then asks a
sequence of questions, any of which can VETO or RESIZE the trade. This module is
that sequence. It deliberately does NOT touch strategy_sw.py, so the hardware
equivalence (A==B==C) of the core signal is preserved; this is a software risk /
execution overlay layered on top, exactly as real systems are built.

The gauntlet, in order (a trade must pass every gate):

  1. FAIR VALUE (microprice) — is the signal confirmed by the size-weighted fair
     price, not just the naive mid? Only buy if microprice sits above mid by at
     least `min_edge_ticks` (and the mirror for sells).
        microprice = (bid_px*ask_sz + ask_px*bid_sz) / (bid_sz + ask_sz)
     Heavier size on the bid pushes microprice up (real buy pressure), etc.

  2. EDGE vs COST — is the expected edge bigger than the cost of trading
     (exchange/broker fee, in bps of notional)? If the edge doesn't clear the
     fee, skip: a marginal signal is not worth paying for.

  3. MARKET CONDITION gates — don't trade into a bad tape:
        * VOLATILITY  — stand down when short-term realized vol spikes (you get
          picked off in fast markets).
        * LIQUIDITY   — require a minimum top-of-book size (thin books slip).
        * STALENESS   — require a fresh quote (don't act on an old price).

  4. INVENTORY SKEW — bias toward flat. Trades that REDUCE the current position
     always pass; trades that ADD are blocked once the position exceeds
     `inventory_soft_frac` of the per-symbol cap. Keeps the book from piling
     one-sided.

  5. PORTFOLIO EXPOSURE + KILL SWITCH — consult the shared Portfolio: clamp the
     size to the remaining gross-notional headroom, and if the daily-loss /
     drawdown kill switch has tripped, stop trading entirely.

Every veto is counted (see `stats`) so the caller can print a funnel:
    N signals -> passed fair value -> passed cost -> ... -> M orders.
That funnel is the whole point — you can SEE the logic thinning the flow.
"""

import math
import time
from collections import deque, Counter
from dataclasses import dataclass, field


# reasons a trade can be vetoed (also the funnel stage names)
FAIR_VALUE = "fair_value"
COST       = "edge_vs_cost"
VOLATILITY = "volatility"
LIQUIDITY  = "liquidity"
STALENESS  = "staleness"
INVENTORY  = "inventory_skew"
EXPOSURE   = "gross_exposure"
KILLED     = "kill_switch"
PASSED     = "passed"


@dataclass
class HFTConfig:
    # 1. fair value
    min_edge_ticks: int = 1          # microprice must beat mid by this many ticks
    # 2. edge vs cost
    fee_bps: float = 1.0             # round-trip-ish fee, basis points of notional
    min_profit_ticks: int = 0        # extra edge required beyond fee
    # 3. market condition
    vol_window: int = 20             # ticks of mid history for realized vol
    max_vol_bps: float = 25.0        # stand down above this short-term vol (bps)
    min_book_size: int = 100         # min shares at the touch (both sides)
    max_quote_age_s: float = 2.0     # quote must be fresher than this
    # 4. inventory skew
    inventory_soft_frac: float = 0.6 # block ADDING once |pos| > frac * cap
    position_cap: int = 1000         # per-symbol share cap (mirror of risk_check)


class SymbolState:
    """Per-symbol rolling state the gates need."""
    def __init__(self, vol_window: int):
        self.mids = deque(maxlen=vol_window)   # recent mid prices (USD)
        self.last_quote_t = 0.0


class PreTradeGauntlet:
    """One gauntlet per LiveFeed; all share one Portfolio for firm-wide limits."""

    def __init__(self, portfolio, cfg: HFTConfig = None):
        self.pf = portfolio
        self.cfg = cfg or HFTConfig()
        self.state: dict[str, SymbolState] = {}
        self.stats = Counter()      # reason -> count (funnel)

    def _st(self, symbol: str) -> SymbolState:
        if symbol not in self.state:
            self.state[symbol] = SymbolState(self.cfg.vol_window)
        return self.state[symbol]

    def observe(self, symbol: str, mid_ticks: int, now: float = None):
        """Feed every quote here (even ones with no signal) so vol/staleness
        history stays continuous."""
        st = self._st(symbol)
        st.mids.append(mid_ticks / 10_000.0)
        st.last_quote_t = now if now is not None else time.time()

    def _realized_vol_bps(self, symbol: str) -> float:
        """Short-term realized volatility of mid returns, in basis points."""
        mids = self._st(symbol).mids
        if len(mids) < 3:
            return 0.0
        rets = [(mids[i] / mids[i - 1] - 1.0) for i in range(1, len(mids))
                if mids[i - 1] > 0]
        if not rets:
            return 0.0
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        return math.sqrt(var) * 10_000.0   # fraction -> bps

    def evaluate(self, symbol, dec, bid_p, bid_s, ask_p, ask_s,
                 position, now=None):
        """Run the raw decision `dec` (a strategy_sw.Decision: action/price/size)
        through every gate. Returns (permitted_decision_or_None, reason).

        `dec` is assumed non-None (a raw signal fired). `position` is the current
        signed share position for this symbol.
        """
        cfg = self.cfg
        now = now if now is not None else time.time()
        mid = (bid_p + ask_p) // 2

        # 5a. KILL SWITCH first — if the desk is halted, nothing trades.
        if self.pf.halted:
            self.stats[KILLED] += 1
            return None, KILLED

        # 1. FAIR VALUE (microprice)
        denom = bid_s + ask_s
        if denom <= 0:
            self.stats[LIQUIDITY] += 1
            return None, LIQUIDITY
        microprice = (bid_p * ask_s + ask_p * bid_s) / denom
        edge_ticks = microprice - mid                     # signed, in ticks
        if dec.action == 0 and edge_ticks < cfg.min_edge_ticks:
            self.stats[FAIR_VALUE] += 1
            return None, FAIR_VALUE
        if dec.action == 1 and -edge_ticks < cfg.min_edge_ticks:
            self.stats[FAIR_VALUE] += 1
            return None, FAIR_VALUE

        # 2. EDGE vs COST — fee in ticks = fee_bps/1e4 * mid_price(ticks)
        fee_ticks = (cfg.fee_bps / 10_000.0) * mid
        if abs(edge_ticks) <= fee_ticks + cfg.min_profit_ticks:
            self.stats[COST] += 1
            return None, COST

        # 3. MARKET CONDITION
        if self._realized_vol_bps(symbol) > cfg.max_vol_bps:
            self.stats[VOLATILITY] += 1
            return None, VOLATILITY
        if bid_s < cfg.min_book_size or ask_s < cfg.min_book_size:
            self.stats[LIQUIDITY] += 1
            return None, LIQUIDITY
        if (now - self._st(symbol).last_quote_t) > cfg.max_quote_age_s:
            self.stats[STALENESS] += 1
            return None, STALENESS

        # 4. INVENTORY SKEW — reducing trades always ok; block adding when heavy
        adding = (position >= 0 and dec.action == 0) or (position <= 0 and dec.action == 1)
        if adding and abs(position) >= cfg.inventory_soft_frac * cfg.position_cap:
            self.stats[INVENTORY] += 1
            return None, INVENTORY

        # 5b. PORTFOLIO GROSS EXPOSURE — clamp size to remaining headroom
        room = self.pf.gross_room(symbol, dec.action, dec.price)
        if room <= 0:
            self.stats[EXPOSURE] += 1
            return None, EXPOSURE
        if dec.size > room:
            dec.size = room                       # resized, still trades

        self.stats[PASSED] += 1
        return dec, PASSED

    # -- reporting ---------------------------------------------------------
    def funnel_lines(self) -> list[str]:
        s = self.stats
        vetoed = sum(v for k, v in s.items() if k != PASSED)
        total = s[PASSED] + vetoed
        lines = ["==== PRE-TRADE FUNNEL (raw signals -> orders) ===="]
        lines.append(f"raw signals evaluated : {total}")
        for reason in (FAIR_VALUE, COST, VOLATILITY, LIQUIDITY, STALENESS,
                       INVENTORY, EXPOSURE, KILLED):
            if s[reason]:
                lines.append(f"  vetoed [{reason:<14}]: {s[reason]}")
        lines.append(f"orders passed         : {s[PASSED]}")
        return lines
