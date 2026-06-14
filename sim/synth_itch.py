"""
Synthetic ITCH 5.0 generator — produces deterministic byte streams for tests.
No NASDAQ download required for iteration.

Usage:
    from synth_itch import SynthITCH
    gen = SynthITCH(symbol=b'AAPL    ')
    stream = gen.add(ref=1, side='B', shares=100, price=1500000)  # $150.0000
    stream += gen.add(ref=2, side='S', shares=80,  price=1500100)
    stream += gen.cancel(ref=1, shares=50)
"""

import struct


class SynthITCH:
    """Builds framed ITCH 5.0 messages (2-byte big-endian length prefix)."""

    def __init__(self, symbol: bytes = b'TEST    ', stock_locate: int = 1):
        self.symbol        = symbol[:8].ljust(8)
        self.stock_locate  = stock_locate
        self._ts           = 34_200_000_000_000  # 9:30 AM in ns

    def _tick(self, delta_ns: int = 1_000) -> bytes:
        self._ts += delta_ns
        # 6-byte big-endian timestamp
        return self._ts.to_bytes(6, 'big')

    def _frame(self, payload: bytes) -> bytes:
        return len(payload).to_bytes(2, 'big') + payload

    def add(self, ref: int, side: str, shares: int, price: int,
            delta_ns: int = 1_000) -> bytes:
        """Add Order (A) message."""
        ts  = self._tick(delta_ns)
        hdr = struct.pack('>BHH', 0x41, self.stock_locate, 0) + ts
        body = struct.pack('>Q', ref)
        body += (b'B' if side == 'B' else b'S')
        body += struct.pack('>I', shares)
        body += self.symbol
        body += struct.pack('>I', price)
        return self._frame(hdr + body)

    def cancel(self, ref: int, shares: int, delta_ns: int = 1_000) -> bytes:
        """Order Cancel (X) message."""
        ts   = self._tick(delta_ns)
        hdr  = struct.pack('>BHH', 0x58, self.stock_locate, 0) + ts
        body = struct.pack('>QI', ref, shares)
        return self._frame(hdr + body)

    def delete(self, ref: int, delta_ns: int = 1_000) -> bytes:
        """Order Delete (D) message."""
        ts   = self._tick(delta_ns)
        hdr  = struct.pack('>BHH', 0x44, self.stock_locate, 0) + ts
        body = struct.pack('>Q', ref)
        return self._frame(hdr + body)

    def execute(self, ref: int, shares: int, match: int = 0,
                delta_ns: int = 1_000) -> bytes:
        """Order Executed (E) message."""
        ts   = self._tick(delta_ns)
        hdr  = struct.pack('>BHH', 0x45, self.stock_locate, 0) + ts
        body = struct.pack('>QIQ', ref, shares, match)
        return self._frame(hdr + body)

    def add_mpid(self, ref: int, side: str, shares: int, price: int,
                 mpid: bytes = b'MMID', delta_ns: int = 1_000) -> bytes:
        """Add Order with MPID Attribution (F) — identical to A plus a
        4-byte attribution field; the parser treats it exactly like an Add."""
        ts  = self._tick(delta_ns)
        hdr = struct.pack('>BHH', 0x46, self.stock_locate, 0) + ts
        body = struct.pack('>Q', ref)
        body += (b'B' if side == 'B' else b'S')
        body += struct.pack('>I', shares)
        body += self.symbol
        body += struct.pack('>I', price)
        body += mpid[:4].ljust(4)               # attribution (ignored by parser)
        return self._frame(hdr + body)

    def execute_with_price(self, ref: int, shares: int, price: int,
                           match: int = 0, printable: bool = True,
                           delta_ns: int = 1_000) -> bytes:
        """Order Executed With Price (C) — like Execute plus an execution
        price (and a printable flag, which the parser ignores)."""
        ts   = self._tick(delta_ns)
        hdr  = struct.pack('>BHH', 0x43, self.stock_locate, 0) + ts
        body = struct.pack('>QIQ', ref, shares, match)
        body += (b'\x01' if printable else b'\x00')
        body += struct.pack('>I', price)
        return self._frame(hdr + body)

    def replace(self, orig_ref: int, new_ref: int, shares: int, price: int,
                delta_ns: int = 1_000) -> bytes:
        """Order Replace (U) — cancels orig_ref and adds new_ref with new
        shares/price. Carries no side; the book reuses the original's side."""
        ts   = self._tick(delta_ns)
        hdr  = struct.pack('>BHH', 0x55, self.stock_locate, 0) + ts
        body = struct.pack('>QQII', orig_ref, new_ref, shares, price)
        return self._frame(hdr + body)

    def trade(self, ref: int, side: str, shares: int, price: int,
              match: int = 0, delta_ns: int = 1_000) -> bytes:
        """Trade, non-cross (P) — a trade print for non-displayable liquidity.
        Same side/shares/price layout as Add, plus an 8-byte match number.
        Informational: the order book leaves it as a no-op."""
        ts  = self._tick(delta_ns)
        hdr = struct.pack('>BHH', 0x50, self.stock_locate, 0) + ts
        body = struct.pack('>Q', ref)
        body += (b'B' if side == 'B' else b'S')
        body += struct.pack('>I', shares)
        body += self.symbol
        body += struct.pack('>I', price)
        body += struct.pack('>Q', match)            # match number (ignored)
        return self._frame(hdr + body)

    def scenario_basic(self) -> bytes:
        """Minimal scenario: two adds (bid+ask), one cancel, one execute."""
        stream  = self.add(ref=1, side='B', shares=200, price=1499900)  # $149.99
        stream += self.add(ref=2, side='S', shares=150, price=1500100)  # $150.01
        stream += self.add(ref=3, side='B', shares=300, price=1499900)  # same bid level
        stream += self.cancel(ref=3, shares=100)
        stream += self.execute(ref=2, shares=50)
        return stream
