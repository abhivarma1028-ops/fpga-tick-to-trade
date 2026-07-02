"""
Order Book Golden Model — M2 (BRAM + RESCAN faithful)

Mirrors rtl/order_book_m2.sv *exactly*, including the behaviours real ITCH data
exercises that the M1 model (order_book.py) ignores:

  * 256-slot DIRECT-MAPPED table addressed by order_ref[7:0]. Order refs alias
    mod 256 — a later Add to a colliding slot overwrites the earlier order, and
    a Cancel/Delete only applies if the slot still holds that exact ref
    (lk_match = entry_valid[idx] && entries[idx].ref == ref).
  * NLEVELS-deep (default 4) sorted level cache per side; same-price orders are
    aggregated into one level (side_insert).
  * Add (A/F) updates the level cache incrementally (O(1)).
  * Cancel(X)/Execute(E)/Exec-Price(C)/Delete(D)/Replace(U) apply the structural
    change to the table, then RESCAN — rebuild the level cache from all valid
    entries — but only when the touched order was in a displayed level (or on
    every Replace). This is the rescan-skip optimisation.

This is the golden reference the Phase-2 replay regression diffs the RTL against.
"""

from dataclasses import dataclass, field
from .itch_parser import ParsedMsg, MsgType


@dataclass
class _Entry:
    order_ref: int
    side: int            # 0 = bid, 1 = ask
    price: int
    shares: int


class _Side:
    """Sorted top-NLEVELS price-level cache for one side (mirrors book_side_t)."""

    def __init__(self, nlevels: int, is_bid: bool):
        self.N = nlevels
        self.is_bid = is_bid
        self.price = [0] * nlevels
        self.size = [0] * nlevels
        self.cnt = 0

    def insert(self, p: int, s: int):
        """Mirror of side_insert(): aggregate same price, else sorted insert."""
        # aggregate into an existing level
        for i in range(self.N):
            if i < self.cnt and self.price[i] == p:
                self.size[i] += s
                return
        # find insertion point (bids sorted desc, asks sorted asc)
        ins = self.cnt
        for i in range(self.N):
            if i < self.cnt and ((p > self.price[i]) if self.is_bid else (p < self.price[i])):
                ins = i
                break
        if ins < self.N:
            for i in range(self.N - 1, ins, -1):
                self.price[i] = self.price[i - 1]
                self.size[i] = self.size[i - 1]
            self.price[ins] = p
            self.size[ins] = s
            if self.cnt < self.N:
                self.cnt += 1

    def best_price(self, empty_val: int) -> int:
        return self.price[0] if self.cnt else empty_val

    def best_size(self) -> int:
        return self.size[0] if self.cnt else 0

    def worst_price(self) -> int:
        widx = (self.cnt - 1) if self.cnt else 0
        return self.price[widx]


@dataclass
class BookSnapshot:
    best_bid_price: int
    best_bid_size: int
    best_ask_price: int
    best_ask_size: int
    book_valid: bool
    # per-level sizes {level0..N-1}; 0 beyond the populated count (matches the
    # RTL generate block that drives unpopulated levels to 0). Consumed by the
    # depth-weighted strategy.
    bid_level_size: list = field(default_factory=list)
    ask_level_size: list = field(default_factory=list)


class OrderBookM2:
    """Cycle-accurate-on-message faithful model of order_book_m2.sv."""

    def __init__(self, order_depth: int = 256, nlevels: int = 4):
        self.DEPTH = order_depth
        self.MASK = order_depth - 1
        self.N = nlevels
        self.entries: list[_Entry | None] = [None] * order_depth
        self.entry_valid = [False] * order_depth
        self.bid = _Side(nlevels, is_bid=True)
        self.ask = _Side(nlevels, is_bid=False)

    # -- structural application + RESCAN ------------------------------------
    def _rescan(self):
        """Rebuild both level caches from every valid entry (RTL RESCAN walk)."""
        self.bid = _Side(self.N, is_bid=True)
        self.ask = _Side(self.N, is_bid=False)
        for idx in range(self.DEPTH):
            if self.entry_valid[idx]:
                e = self.entries[idx]
                if e.side == 0:
                    self.bid.insert(e.price, e.shares)
                else:
                    self.ask.insert(e.price, e.shares)

    def _affects_displayed(self, e: _Entry) -> bool:
        """Mirror aff_displayed: would touching this order change a shown level?"""
        if e.side == 1:  # ask
            return (self.ask.cnt != self.N) or (e.price <= self.ask.worst_price())
        else:            # bid
            return (self.bid.cnt != self.N) or (e.price >= self.bid.worst_price())

    # -- message application -------------------------------------------------
    def apply(self, msg: ParsedMsg):
        mt = msg.msg_type
        idx = msg.order_ref & self.MASK

        if mt in (MsgType.ADD, MsgType.ADD_MPID):
            self.entries[idx] = _Entry(msg.order_ref, msg.side, msg.price, msg.shares)
            self.entry_valid[idx] = True
            if msg.side == 0:
                self.bid.insert(msg.price, msg.shares)
            else:
                self.ask.insert(msg.price, msg.shares)
            return

        # Trade(P) is informational — book no-op (matches RTL default ignore)
        if mt == MsgType.TRADE:
            return

        # C/D/E/U/X — read target, match on exact ref
        if not (self.entry_valid[idx] and self.entries[idx].order_ref == msg.order_ref):
            return  # lk_match fails — no-op

        orig = self.entries[idx]                 # rd_data (pre-change)
        is_reduce = mt in (MsgType.CANCEL, MsgType.EXECUTE, MsgType.EXEC_PRICE)
        is_delete = mt == MsgType.DELETE
        is_repl = mt == MsgType.REPLACE

        # aff_displayed is evaluated against the ORIGINAL entry + current cache
        rescan = is_repl or self._affects_displayed(orig)

        if is_reduce:
            if orig.shares > msg.shares:
                self.entries[idx].shares = orig.shares - msg.shares   # partial
            else:
                self.entry_valid[idx] = False                         # fully gone
        elif is_delete:
            self.entry_valid[idx] = False
        elif is_repl:
            nidx = msg.new_order_ref & self.MASK
            self.entry_valid[idx] = False
            self.entries[nidx] = _Entry(msg.new_order_ref, orig.side, msg.price, msg.shares)
            self.entry_valid[nidx] = True

        if rescan:
            self._rescan()

    # -- outputs -------------------------------------------------------------
    def _level_sizes(self, side: _Side) -> list:
        return [side.size[i] if i < side.cnt else 0 for i in range(self.N)]

    def snapshot(self) -> BookSnapshot:
        return BookSnapshot(
            best_bid_price=self.bid.best_price(0),
            best_bid_size=self.bid.best_size(),
            best_ask_price=self.ask.best_price(0xFFFF_FFFF),
            best_ask_size=self.ask.best_size(),
            book_valid=(self.bid.cnt > 0 and self.ask.cnt > 0),
            bid_level_size=self._level_sizes(self.bid),
            ask_level_size=self._level_sizes(self.ask),
        )
