"""
Top-of-Book Golden Model — M1
Mirrors order_book_top.sv exactly so cocotb can diff RTL output against this.
"""

from dataclasses import dataclass, field
from .itch_parser import ParsedMsg, MsgType


@dataclass
class BookState:
    best_bid_price: int = 0
    best_bid_size:  int = 0
    best_ask_price: int = 0xFFFF_FFFF
    best_ask_size:  int = 0
    bid_seen:       bool = False
    ask_seen:       bool = False

    @property
    def valid(self) -> bool:
        return self.bid_seen and self.ask_seen


class OrderBook:
    """Top-of-book tracker matching RTL order_book_top.sv behaviour."""

    def __init__(self):
        # order_ref → (price, shares, side)
        self._orders: dict[int, tuple[int, int, int]] = {}
        self.state = BookState()

    def apply(self, msg: ParsedMsg) -> BookState:
        s = self.state

        if msg.msg_type == MsgType.ADD:
            self._orders[msg.order_ref] = (msg.price, msg.shares, msg.side)
            if msg.side == 0:  # buy
                s.bid_seen = True
                if msg.price > s.best_bid_price or not s.bid_seen:
                    s.best_bid_price = msg.price
                    s.best_bid_size  = msg.shares
                elif msg.price == s.best_bid_price:
                    s.best_bid_size += msg.shares
            else:              # sell
                s.ask_seen = True
                if msg.price < s.best_ask_price or not s.ask_seen:
                    s.best_ask_price = msg.price
                    s.best_ask_size  = msg.shares
                elif msg.price == s.best_ask_price:
                    s.best_ask_size += msg.shares

        elif msg.msg_type == MsgType.CANCEL:
            if msg.order_ref in self._orders:
                p, sh, side = self._orders[msg.order_ref]
                if sh <= msg.shares:
                    del self._orders[msg.order_ref]
                else:
                    self._orders[msg.order_ref] = (p, sh - msg.shares, side)

        elif msg.msg_type == MsgType.DELETE:
            self._orders.pop(msg.order_ref, None)

        elif msg.msg_type == MsgType.EXECUTE:
            if msg.order_ref in self._orders:
                p, sh, side = self._orders[msg.order_ref]
                if sh <= msg.shares:
                    del self._orders[msg.order_ref]
                else:
                    self._orders[msg.order_ref] = (p, sh - msg.shares, side)

        return s

    def snapshot(self) -> BookState:
        return BookState(
            best_bid_price = self.state.best_bid_price,
            best_bid_size  = self.state.best_bid_size,
            best_ask_price = self.state.best_ask_price,
            best_ask_size  = self.state.best_ask_size,
            bid_seen       = self.state.bid_seen,
            ask_seen       = self.state.ask_seen,
        )
