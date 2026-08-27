"""
Golden model — bit-exact mirror of rtl/strategy_market_maker.sv.

Integer-only, matching the RTL's unsigned truncating division (Verilog `/` on
positive operands == Python `//`), so cocotb can assert the hardware market maker
reproduces this model decision-for-decision.
"""


def mm_quote(book_valid, best_bid_price, best_ask_price, bid_size, ask_size,
             inventory, HALF_SPREAD=200, SKEW=2, QUOTE_SIZE=100, MAX_POSITION=1000):
    """Return (quote_valid, bid_price, bid_qty, ask_price, ask_qty)."""
    den = bid_size + ask_size
    normal = bool(book_valid) and (best_ask_price > best_bid_price) and (den != 0)
    if not normal:
        return (0, 0, 0, 0, 0)

    num = best_bid_price * ask_size + best_ask_price * bid_size
    micro = num // den                                   # unsigned truncating divide
    centre = micro - inventory * SKEW
    bid = centre - HALF_SPREAD
    ask = centre + HALF_SPREAD
    if ask <= bid:
        ask = bid + 1

    bidq, askq = QUOTE_SIZE, QUOTE_SIZE
    if inventory + QUOTE_SIZE > MAX_POSITION:
        bidq = 0
    if inventory - QUOTE_SIZE < -MAX_POSITION:
        askq = 0

    bidp = 0 if bid < 0 else bid
    askp = 0 if ask < 0 else ask
    if bidq == 0:
        bidp = 0
    if askq == 0:
        askp = 0
    return (1, bidp, bidq, askp, askq)
