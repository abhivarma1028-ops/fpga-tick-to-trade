"""
ITCH 5.0 Golden Reference Parser — M2 subset
Handles: Add(A), Add+MPID(F), Cancel(X), Delete(D), Execute(E),
         Execute-with-Price(C), Replace(U)
Returns dataclasses matching the RTL output signals exactly.
"""

import struct
from dataclasses import dataclass
from enum import IntEnum


class MsgType(IntEnum):
    ADD        = 0x41  # 'A'
    EXEC_PRICE = 0x43  # 'C'  (Order Executed with Price)
    DELETE     = 0x44  # 'D'
    EXECUTE    = 0x45  # 'E'
    ADD_MPID   = 0x46  # 'F'  (Add with MPID attribution)
    TRADE      = 0x50  # 'P'  (Trade, non-cross — informational print)
    REPLACE    = 0x55  # 'U'  (Order Replace)
    CANCEL     = 0x58  # 'X'


@dataclass
class ParsedMsg:
    msg_type:      int   # MsgType value
    timestamp:     int   # 48-bit ns since midnight
    order_ref:     int   # 64-bit order reference (original/target)
    side:          int   # 0=buy 1=sell  (0 for non-Add)
    shares:        int   # quantity
    price:         int   # fixed-point /10000 = USD  (0 when not carried)
    new_order_ref: int = 0  # Replace(U): the NEW ref; else == order_ref


# Message lengths (bytes, excluding the 2-byte length prefix)
_MSG_LEN = {
    MsgType.ADD:        36,
    MsgType.ADD_MPID:   40,
    MsgType.EXEC_PRICE: 36,
    MsgType.CANCEL:     23,
    MsgType.DELETE:     19,
    MsgType.EXECUTE:    31,
    MsgType.REPLACE:    35,
    MsgType.TRADE:      44,
}

SUPPORTED = set(MsgType)


def parse_message(raw: bytes) -> ParsedMsg | None:
    """Parse one raw ITCH message (no length prefix).  Returns None if unsupported."""
    if len(raw) < 1:
        return None
    msg_type = raw[0]
    if msg_type not in SUPPORTED:
        return None

    # Common header for every supported type:
    # [0] msg_type [1:3] locate [3:5] tracking [5:11] timestamp(6B) [11:19] order_ref(8B)
    ts  = int.from_bytes(raw[5:11],  'big')
    ref = int.from_bytes(raw[11:19], 'big')

    if msg_type in (MsgType.ADD, MsgType.ADD_MPID, MsgType.TRADE):
        # Add / Add+MPID / Trade share side@19, shares@20-23, price@32-35.
        # F has 4 trailing MPID bytes; P has an 8-byte match number — both ignored.
        side   = 1 if raw[19] == ord('S') else 0
        shares = int.from_bytes(raw[20:24], 'big')
        # raw[24:32] = stock symbol (ignored)
        price  = int.from_bytes(raw[32:36], 'big')
        return ParsedMsg(msg_type, ts, ref, side, shares, price, ref)

    elif msg_type == MsgType.EXEC_PRICE:
        # exec shares@19-22, match#@23-30, printable@31, exec price@32-35
        shares = int.from_bytes(raw[19:23], 'big')
        price  = int.from_bytes(raw[32:36], 'big')
        return ParsedMsg(msg_type, ts, ref, 0, shares, price, ref)

    elif msg_type == MsgType.REPLACE:
        # new ref@19-26, shares@27-30, price@31-34; carries no side
        new_ref = int.from_bytes(raw[19:27], 'big')
        shares  = int.from_bytes(raw[27:31], 'big')
        price   = int.from_bytes(raw[31:35], 'big')
        return ParsedMsg(msg_type, ts, ref, 0, shares, price, new_ref)

    elif msg_type == MsgType.CANCEL:
        shares = int.from_bytes(raw[19:23], 'big')
        return ParsedMsg(msg_type, ts, ref, 0, shares, 0, ref)

    elif msg_type == MsgType.DELETE:
        return ParsedMsg(msg_type, ts, ref, 0, 0, 0, ref)

    elif msg_type == MsgType.EXECUTE:
        shares = int.from_bytes(raw[19:23], 'big')
        return ParsedMsg(msg_type, ts, ref, 0, shares, 0, ref)


def parse_stream(data: bytes) -> list[ParsedMsg]:
    """Parse a byte stream of framed ITCH messages (2-byte big-endian length prefix each)."""
    results = []
    offset  = 0
    while offset + 2 <= len(data):
        length = int.from_bytes(data[offset:offset+2], 'big')
        offset += 2
        if offset + length > len(data):
            break
        raw = data[offset:offset+length]
        offset += length
        msg = parse_message(raw)
        if msg is not None:
            results.append(msg)
    return results
