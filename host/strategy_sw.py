"""
Software mirror of rtl/strategy_imbalance.sv.

Bit-for-bit faithful to the RTL so the live software path produces the same
decisions the FPGA would. Same cross-multiply (no division), same prev_valid
2-cycle gate, same LOT_SIZE, same aggressive-price selection.

Used by live_feed.py to run the strategy on live IBKR market data, and is the
golden reference for tb_strategy_imbalance.py.

Fixed-point convention (matches the RTL and ITCH):
    price is an integer in units of 1/10,000 USD  (i.e. price_usd * 10_000)
    size  is an integer share count
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Decision:
    action: int       # 0 = BUY, 1 = SELL
    price:  int       # order limit price, ITCH fixed-point (price_usd * 10_000)
    size:   int       # shares


class SoftwareStrategy:
    """Mirrors strategy_imbalance.sv exactly.

    RTL trigger conditions (cross-multiply form, no division):
        BUY : best_bid_size * 10 > BID_THRESH * best_ask_size  &&  best_ask_price > 0
        SELL: best_ask_size * 10 > ASK_THRESH * best_bid_size  &&  best_bid_price > 0

    The RTL gates every decision behind `book_valid && prev_valid`, i.e. the book
    must have been valid for two consecutive evaluations. We reproduce that with
    the `_prev_valid` member.
    """

    LOT_SIZE = 100   # shares per order — matches localparam LOT_SIZE in the RTL

    def __init__(self, bid_thresh: int = 15, ask_thresh: int = 15):
        self.BID_THRESH = bid_thresh
        self.ASK_THRESH = ask_thresh
        self._prev_valid = False

    def evaluate(self, book_valid: bool,
                 best_bid_price: int, best_bid_size: int,
                 best_ask_price: int, best_ask_size: int) -> Optional[Decision]:
        """One evaluation = one clock cycle in the RTL.

        Returns a Decision on the cycles the RTL would pulse decision_valid,
        else None. Call this once per market-data update.
        """
        # In the RTL, decision_valid defaults to 0 and prev_valid latches
        # book_valid every cycle. The gate uses the *previous* prev_valid.
        gate = book_valid and self._prev_valid
        self._prev_valid = book_valid

        if not gate:
            return None

        # BUY: bid dominates the book (buyers outnumber sellers)
        if (best_bid_size * 10 > self.BID_THRESH * best_ask_size
                and best_ask_price > 0):
            return Decision(action=0,
                            price=best_ask_price,   # lift the ask (aggressive)
                            size=self.LOT_SIZE)

        # SELL: ask dominates the book
        if (best_ask_size * 10 > self.ASK_THRESH * best_bid_size
                and best_bid_price > 0):
            return Decision(action=1,
                            price=best_bid_price,   # hit the bid (aggressive)
                            size=self.LOT_SIZE)

        return None

    def reset(self):
        """Clear the prev_valid gate (e.g. after a feed gap)."""
        self._prev_valid = False
