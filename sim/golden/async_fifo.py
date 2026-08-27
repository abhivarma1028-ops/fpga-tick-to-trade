"""
Golden reference for rtl/cdc/async_fifo.sv (and axis_async_fifo.sv).

A dual-clock FIFO's *timing* (exactly when full/empty assert) is non-deterministic
across unrelated clocks, so the golden does NOT model cycle-accurate flags. What it
DOES pin down is the only thing that must hold regardless of clock ratio:

    a FIFO preserves order and neither drops nor duplicates data.

The testbench drives a known push sequence, records the pop sequence, and asserts
the pops are an in-order prefix of the pushes (via `check_prefix`).
"""
from collections import deque


class AsyncFifoModel:
    """Bounded FIFO queue — ordering/integrity reference (not flag timing)."""
    def __init__(self, depth):
        self.depth = depth
        self.q = deque()

    def full(self):
        return len(self.q) >= self.depth

    def empty(self):
        return len(self.q) == 0

    def push(self, v):
        assert not self.full(), "golden overflow — TB pushed while full"
        self.q.append(v)

    def pop(self):
        assert not self.empty(), "golden underflow — TB popped while empty"
        return self.q.popleft()


def check_prefix(pushed, popped):
    """Assert `popped` is an in-order, loss/dup-free prefix of `pushed`."""
    assert len(popped) <= len(pushed), (
        f"popped more ({len(popped)}) than pushed ({len(pushed)}) — duplication")
    for i, (a, b) in enumerate(zip(pushed, popped)):
        assert a == b, f"order/integrity break at index {i}: pushed {a!r} != popped {b!r}"
