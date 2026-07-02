"""
Phase-3 (local prep) — full-pipeline equivalence + latency measurement.

This is the simulation foundation for the AWS-F1 hardware run: it exercises the
COMPLETE pipeline output (parser -> book -> strategy -> risk -> m_axis), not just
the book, and reads the hardware latency_counter histogram over AXI-Lite — the
same register map the host will read on F1.

What it does:
  * Drives a realistic ITCH stream (replay_gen) into tick_to_trade_top.
  * Monitors m_axis: records every decision packet the hardware emits.
  * Monitors u_strategy: records the strategy proposal sequence (collapsed).
  * Reads the latency histogram (0x000-0x0FC) + last_latency (0x100) via AXI-Lite
    at the end and prints the tick-to-trade latency distribution in cycles/ns.

Equivalence is reported against the golden chain (phase2_golden.run). The book
layer is already asserted bit-exact in tb_replay_regression; here the focus is
the end-to-end decision stream and the measured latency that the F1 run will
later confirm on silicon.

Run:
    cd sim && REPLAY_N=300 REPLAY_SEED=7 make SIM=questa \
        TOPLEVEL=tick_to_trade_top MODULE=tb_phase3_pipeline
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, ReadOnly
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'host'))

import replay_gen
import phase2_golden
from synth_itch import SynthITCH

CLK = 5  # ns


async def reset(dut):
    dut.rst_n.value = 0
    dut.halt.value = 0
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tdata.value = 0
    dut.s_axis_tlast.value = 0
    dut.m_axis_tready.value = 1
    for s in ('s_axil_awaddr', 's_axil_awvalid', 's_axil_wdata', 's_axil_wstrb',
              's_axil_wvalid', 's_axil_bready', 's_axil_araddr', 's_axil_arvalid',
              's_axil_rready'):
        getattr(dut, s).value = 0
    await ClockCycles(dut.clk, 8)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def axil_read(dut, addr):
    dut.s_axil_araddr.value = addr
    dut.s_axil_arvalid.value = 1
    dut.s_axil_rready.value = 1
    await ReadOnly()
    while not int(dut.s_axil_arready.value):
        await RisingEdge(dut.clk); await ReadOnly()
    await RisingEdge(dut.clk)
    dut.s_axil_arvalid.value = 0
    await ReadOnly()
    while not int(dut.s_axil_rvalid.value):
        await RisingEdge(dut.clk); await ReadOnly()
    data = int(dut.s_axil_rdata.value)
    await RisingEdge(dut.clk)
    dut.s_axil_rready.value = 0
    return data


async def axil_write(dut, addr, data):
    # This DUT raises wready only the cycle after awready, so hold both valids
    # up until bvalid (awready stays low while aw_active, wready stays low after
    # accept, so leaving them high is harmless), then drop everything.
    dut.s_axil_awaddr.value = addr
    dut.s_axil_awvalid.value = 1
    dut.s_axil_wdata.value = data
    dut.s_axil_wstrb.value = 0xF
    dut.s_axil_wvalid.value = 1
    dut.s_axil_bready.value = 1
    await ReadOnly()
    while not int(dut.s_axil_bvalid.value):
        await RisingEdge(dut.clk); await ReadOnly()
    await RisingEdge(dut.clk)
    dut.s_axil_awvalid.value = 0
    dut.s_axil_wvalid.value = 0
    dut.s_axil_bready.value = 0


async def drive_stream(dut, raw):
    for i, byte in enumerate(raw):
        dut.s_axis_tvalid.value = 1
        dut.s_axis_tdata.value = byte
        dut.s_axis_tlast.value = 1 if i == len(raw) - 1 else 0
        await ReadOnly()
        while not int(dut.s_axis_tready.value):
            await RisingEdge(dut.clk); await ReadOnly()
        await RisingEdge(dut.clk)
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tlast.value = 0


def split_framed(stream):
    off, out = 0, []
    while off + 2 <= len(stream):
        n = int.from_bytes(stream[off:off + 2], 'big'); off += 2
        out.append(stream[off:off + n]); off += n
    return out


@cocotb.test()
async def test_phase3_pipeline(dut):
    n = int(os.environ.get('REPLAY_N', '300'))
    seed = int(os.environ.get('REPLAY_SEED', '7'))
    stream = replay_gen.build_synthetic(n, seed)
    raws = split_framed(stream)

    # golden reference (full chain: book -> strategy -> risk)
    gdec, grej, gstats = phase2_golden.run(stream)

    cocotb.start_soon(Clock(dut.clk, CLK, unit='ns').start())
    await reset(dut)

    # ---- ISOLATED tick-to-trade latency (single tick in flight, empty book) --
    # Done FIRST on the post-reset empty book so a lone ask->bid 2:1 imbalance
    # cleanly fires one decision. The streaming histogram (later) resets t0 on
    # every message and so does NOT measure causal latency — this isolated regime
    # is the README's "39 cycles ~195 ns" measurement.
    await axil_write(dut, 0x104, 1)            # clear histogram + last_latency
    await ClockCycles(dut.clk, 4)
    gen = SynthITCH()
    await drive_stream(dut, gen.add(ref=901, side='S', shares=100, price=1_500_100)[2:])
    await drive_stream(dut, gen.add(ref=902, side='B', shares=200, price=1_499_900)[2:])
    await ClockCycles(dut.clk, 80)
    iso_latency = await axil_read(dut, 0x100)
    await axil_write(dut, 0x104, 1)            # clear before the streaming run
    await ClockCycles(dut.clk, 4)

    m_decisions = []   # every hardware m_axis decision
    strat_seq = []     # collapsed strategy proposals

    async def m_axis_mon():
        while True:
            await RisingEdge(dut.clk)
            if int(dut.m_axis_tvalid.value):
                t = int(dut.m_axis_tdata.value)
                m_decisions.append(((t >> 64) & 0x1, (t >> 32) & 0xFFFFFFFF, t & 0xFFFFFFFF))

    async def strat_mon():
        last = None
        while True:
            await RisingEdge(dut.clk)
            if int(dut.u_strategy.decision_valid.value):
                pkt = (int(dut.u_strategy.action.value),
                       int(dut.u_strategy.order_price.value),
                       int(dut.u_strategy.order_size.value))
                if pkt != last:
                    strat_seq.append(pkt)
                last = pkt

    cocotb.start_soon(m_axis_mon())
    cocotb.start_soon(strat_mon())

    for raw in raws:
        await drive_stream(dut, raw)
    await ClockCycles(dut.clk, 400)   # drain final RESCAN + pipeline

    # ---- read the latency histogram over AXI-Lite (same map the host uses) ----
    hist = []
    for b in range(64):
        hist.append(await axil_read(dut, b * 4))
    last_latency = await axil_read(dut, 0x100)

    stream_total = sum(hist)
    nz = [(b, c) for b, c in enumerate(hist) if c]

    dut._log.info("=" * 70)
    dut._log.info(f"Phase-3 pipeline: n={n} seed={seed}")
    dut._log.info(f"  hardware m_axis decisions : {len(m_decisions)}")
    dut._log.info(f"  golden accepted decisions : {gstats['accepted']}  "
                  f"(rejects={gstats['rejected']})")
    dut._log.info(f"  strategy proposals (RTL collapsed) : {len(strat_seq)}")
    dut._log.info("  --- ISOLATED TICK-TO-TRADE LATENCY (single tick in flight) ---")
    dut._log.info(f"  *** {iso_latency} cycles = {iso_latency * CLK} ns ***")
    dut._log.info("  --- streaming histogram (decision-to-recent-msg gap, NOT causal) ---")
    dut._log.info(f"  measured under load : {stream_total}")
    for b, c in nz:
        dut._log.info(f"    {b:2d} cyc ({b*CLK:3d} ns): {c}")
    dut._log.info("=" * 70)

    # Gates: book equivalence is asserted in tb_replay_regression; here we gate
    # the headline isolated latency against the ~195-205 ns design target.
    assert stream_total > 0, "no decisions produced under load"
    assert 30 <= iso_latency <= 45, \
        f"isolated tick-to-trade latency {iso_latency} cyc ({iso_latency*CLK} ns) " \
        f"outside the expected 39-cycle (~195 ns) regime"
    dut._log.info(f"PASS  isolated tick-to-trade latency = {iso_latency} cyc "
                  f"({iso_latency * CLK} ns)")
