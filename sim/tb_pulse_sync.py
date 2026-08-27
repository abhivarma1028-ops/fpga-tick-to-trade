"""
cocotb: rtl/cdc/pulse_sync.sv — single-cycle strobe across clock domains.

Exercised BOTH directions (source faster than destination and vice-versa). The
source launches N strobes, each only when `src_busy` is low (respecting the
closed-loop handshake); a destination monitor counts `dst_pulse` events. Pass
condition: exactly one destination pulse per accepted source pulse — no drops,
no double-counts.

Run: cd sim && make cdc-run TOPLEVEL=pulse_sync MODULE=tb_pulse_sync
"""
import random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, Timer


class Counter:
    def __init__(self):
        self.n = 0


async def _reset(dut):
    dut.src_rst_n.value = 0
    dut.dst_rst_n.value = 0
    dut.src_pulse.value = 0
    await Timer(50, unit="ns")
    dut.src_rst_n.value = 1
    dut.dst_rst_n.value = 1
    await RisingEdge(dut.src_clk)
    await RisingEdge(dut.dst_clk)


async def _dst_monitor(dut, cnt):
    # sample mid-cycle (falling edge): dst_pulse is a combinational one-cycle
    # strobe; reading it at the rising edge is delta-sensitive.
    while True:
        await FallingEdge(dut.dst_clk)
        if int(dut.dst_pulse.value):
            cnt.n += 1


async def _run(dut, src_ns, dst_ns, n=60, seed=1):
    cocotb.start_soon(Clock(dut.src_clk, src_ns, unit="ns").start())
    cocotb.start_soon(Clock(dut.dst_clk, dst_ns, unit="ns").start())
    await _reset(dut)

    got = Counter()
    cocotb.start_soon(_dst_monitor(dut, got))
    rng = random.Random(seed)

    # Drive on the falling edge so the value is stable at the sampling rising
    # edge (cocotb+Verilator applies writes with a one-edge visibility delay).
    dut.src_pulse.value = 0
    sent = 0
    for _ in range(n):
        for _ in range(rng.randint(0, 4)):          # random inter-pulse gap
            await FallingEdge(dut.src_clk)
        await FallingEdge(dut.src_clk)
        while int(dut.src_busy.value):              # respect the handshake
            await FallingEdge(dut.src_clk)
        dut.src_pulse.value = 1
        await RisingEdge(dut.src_clk)               # DUT samples pulse=1 & !busy -> toggle
        dut.src_pulse.value = 0
        await FallingEdge(dut.src_clk)
        assert int(dut.src_busy.value) == 1, "toggle not accepted (busy stayed low)"
        sent += 1

    # wait for full quiescence: last strobe fully round-tripped, then flush
    while int(dut.src_busy.value):
        await FallingEdge(dut.src_clk)
    for _ in range(60):
        await RisingEdge(dut.dst_clk)

    assert got.n == sent, f"dst pulses {got.n} != src pulses {sent}"
    dut._log.info(f"pulse_sync: {sent} strobes crossed, {got.n} received "
                  f"(src={src_ns}ns dst={dst_ns}ns)")


@cocotb.test()
async def fast_to_slow(dut):
    await _run(dut, src_ns=5, dst_ns=13, seed=2)


@cocotb.test()
async def slow_to_fast(dut):
    await _run(dut, src_ns=13, dst_ns=5, seed=3)
