"""
Golden reference for rtl/cdc/handshake_mcp.sv.

The MCP handshake must deliver every *accepted* source word to the destination
exactly once and in order (no drop, no duplicate, no torn/half-updated value).
Throughput is round-trip limited, so the destination may observe fewer updates
than the source attempted — but the ones it observes are an in-order prefix of the
accepted words. The testbench records accepted source words and captured
destination words and compares with `check_prefix`.
"""


def check_prefix(accepted, captured):
    """Assert dst-captured words are an in-order prefix of src-accepted words."""
    assert len(captured) <= len(accepted), (
        f"captured more ({len(captured)}) than accepted ({len(accepted)})")
    for i, (a, b) in enumerate(zip(accepted, captured)):
        assert a == b, f"MCP mismatch at update {i}: accepted {a:#x} != captured {b:#x}"
