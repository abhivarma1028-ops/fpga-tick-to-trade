"""
Market-making strategy — the profitable counterpart to the aggressive taker.

The imbalance strategy in strategy_sw.py CROSSES the spread (it lifts the ask /
hits the bid), so it PAYS the spread on every trade — after fees that is roughly
break-even, as the dashboard's equity curve shows. A market maker does the
opposite: it POSTS resting quotes on both sides and EARNS the spread when both a
buyer and a seller trade against it.

Quoting logic (all integer ticks; ITCH fixed-point USD*10_000):

  fair   = microprice = (bid_px*ask_sz + ask_px*bid_sz) / (bid_sz + ask_sz)
  skew   = -inventory * skew_ticks_per_share          # lean AWAY from inventory
  centre = fair + skew
  bid    = centre - half_spread_ticks
  ask    = centre + half_spread_ticks

Inventory skew is the heart of a real market maker: when it is long, it lowers
BOTH quotes so its ask is keener (more likely to sell) and its bid is shy (less
likely to buy more) — pushing inventory back toward flat. When short, it raises
both. Without skew a maker accumulates a runaway position on any trend and bleeds
on the mark-to-market; skew is what keeps it alive.

This module is pure quoting logic. Fills are decided by the backtest's fill model
(host/backtest.py); PnL/inventory are tracked by the shared Portfolio.
"""

from dataclasses import dataclass


@dataclass
class MMConfig:
    half_spread_ticks: int = 3       # distance of each quote from the centre
    # INTEGER ticks per share, matching the RTL's SKEW (golden model uses 2).
    # Was 0.02, which the hardware cannot express -- under rtl_exact it truncates
    # to 0 and the inventory skew silently stops working. 2 is the value the RTL
    # actually implements, so this is what the FPGA would do.
    skew_ticks_per_share: float = 2.0   # how hard to lean per share of inventory
    quote_size: int = 100            # shares posted on each side
    max_position: int = 1000         # stop quoting the side that would breach this
    min_book_size: int = 1           # don't quote into an empty/degenerate book
    # Mirror strategy_market_maker.sv exactly (integer maths, truncating divide).
    # Defaults True because the whole point of this module is to reproduce what
    # the FPGA would do; set False only to deliberately diverge from hardware.
    #
    # NOTE the RTL's SKEW is an INTEGER tick count (golden model uses 2). With
    # rtl_exact the fractional default below truncates to 0, i.e. no skew --
    # set skew_ticks_per_share to a whole number of ticks to actually lean.
    rtl_exact: bool = True


@dataclass
class Quote:
    bid_price: int   # ticks; 0 = not quoting this side
    bid_size: int
    ask_price: int   # ticks; 0 = not quoting this side
    ask_size: int


class MarketMaker:
    def __init__(self, cfg: MMConfig = None):
        self.cfg = cfg or MMConfig()

    @staticmethod
    def microprice(bid_p, bid_s, ask_p, ask_s) -> float:
        denom = bid_s + ask_s
        if denom <= 0:
            return (bid_p + ask_p) / 2.0
        return (bid_p * ask_s + ask_p * bid_s) / denom

    def quote(self, bid_p, bid_s, ask_p, ask_s, inventory: int) -> Quote:
        """Return the two-sided quote given the current market and our inventory.

        A side is suppressed (price 0) when adding there would breach max_position,
        so the maker naturally stops buying when maxed long / selling when maxed
        short — a soft position guard baked into the quoting itself.
        """
        cfg = self.cfg
        if bid_s < cfg.min_book_size or ask_s < cfg.min_book_size or \
           not (ask_p > bid_p):
            return Quote(0, 0, 0, 0)

        if cfg.rtl_exact:
            # INTEGER arithmetic throughout, matching strategy_market_maker.sv
            # (and sim/golden/mm_rtl.py) bit for bit. The RTL has no floating
            # point: the microprice is an unsigned truncating divide and the skew
            # is an integer tick count.
            #
            # Computing this in float and rounding put EVERY quote one tick away
            # from what the hardware posts -- 9,897 of 9,897 quotes differed
            # before this was fixed (sim/tb_strategy_equiv_all.py). One tick on
            # both sides is the difference between getting filled and not, so a
            # "mirror" that does this is not a mirror.
            num    = bid_p * ask_s + ask_p * bid_s
            den    = bid_s + ask_s
            micro  = num // den
            centre = micro - inventory * int(cfg.skew_ticks_per_share)
            bid    = centre - int(cfg.half_spread_ticks)
            ask    = centre + int(cfg.half_spread_ticks)
        else:
            # Float path: finer skew granularity than the hardware can express.
            # Deliberately NOT equivalent to the RTL -- only use this if you have
            # decided you want the software to behave differently.
            fair   = self.microprice(bid_p, bid_s, ask_p, ask_s)
            centre = fair - inventory * cfg.skew_ticks_per_share
            bid    = int(round(centre - cfg.half_spread_ticks))
            ask    = int(round(centre + cfg.half_spread_ticks))

        # never quote a crossed/locked book of our own
        if ask <= bid:
            ask = bid + 1

        q = Quote(bid, cfg.quote_size, ask, cfg.quote_size)
        # position guard: don't post the side that would push past the cap
        if inventory + cfg.quote_size > cfg.max_position:
            q.bid_price, q.bid_size = 0, 0      # can't buy more
        if inventory - cfg.quote_size < -cfg.max_position:
            q.ask_price, q.ask_size = 0, 0      # can't sell more
        return q
