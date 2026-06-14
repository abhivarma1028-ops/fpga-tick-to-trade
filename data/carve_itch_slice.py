"""
Carve a small single-symbol slice from a full NASDAQ TotalView-ITCH 5.0 file.
The raw files are ~multi-GB .gz; this script extracts only the messages for one
symbol so the sim can iterate on a small deterministic dataset.

Usage:
    python carve_itch_slice.py --input S081322-v50.txt.gz \
                                --symbol AAPL \
                                --max-msgs 50000 \
                                --output aapl_slice.bin

Output: raw ITCH binary (framed, 2-byte big-endian length per message),
        containing only Add/Cancel/Delete/Execute messages for the given symbol.

Download samples from: https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH/
"""

import gzip
import struct
import argparse
import sys

SUPPORTED_TYPES = {0x41, 0x44, 0x45, 0x58}  # A D E X

# Stock field offsets and lengths for each message type
# (only Add has a stock field; for others we filter by order_ref after first pass)
ADD_STOCK_OFFSET = 24   # bytes 24-31 in Add message


def get_stock(raw: bytes) -> bytes:
    """Extract the 8-byte stock symbol from an Add message."""
    return raw[ADD_STOCK_OFFSET:ADD_STOCK_OFFSET+8]


def carve(src: str, symbol: str, max_msgs: int, dst: str):
    sym_bytes = symbol.upper().ljust(8).encode('ascii')
    tracked_refs: set[int] = set()
    out_messages = 0

    opener = gzip.open if src.endswith('.gz') else open

    with opener(src, 'rb') as fin, open(dst, 'wb') as fout:
        while out_messages < max_msgs:
            hdr = fin.read(2)
            if len(hdr) < 2:
                break
            length = int.from_bytes(hdr, 'big')
            raw    = fin.read(length)
            if len(raw) < length:
                break

            if not raw:
                continue
            msg_type = raw[0]
            if msg_type not in SUPPORTED_TYPES:
                continue

            keep = False
            if msg_type == 0x41:  # Add
                if get_stock(raw) == sym_bytes:
                    ref = int.from_bytes(raw[11:19], 'big')
                    tracked_refs.add(ref)
                    keep = True
            else:
                ref = int.from_bytes(raw[11:19], 'big')
                if ref in tracked_refs:
                    keep = True
                    if msg_type == 0x44:  # Delete — stop tracking
                        tracked_refs.discard(ref)

            if keep:
                fout.write(hdr + raw)
                out_messages += 1

    print(f"Wrote {out_messages} messages to {dst}  "
          f"({len(tracked_refs)} orders still open at end)")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--input',    required=True)
    ap.add_argument('--symbol',   default='AAPL')
    ap.add_argument('--max-msgs', type=int, default=50_000)
    ap.add_argument('--output',   default='slice.bin')
    args = ap.parse_args()
    carve(args.input, args.symbol, args.max_msgs, args.output)
