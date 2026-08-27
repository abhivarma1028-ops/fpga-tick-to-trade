#!/usr/bin/env python3
"""Check a packaged CDC IP's component.xml for the things that actually matter.

Reads IP-XACT only -- never launches Vivado. Run after each packaging pass:

    python3 ip/check_packaged_ip.py <ip-dir-or-component.xml> [...]
    python3 ip/check_packaged_ip.py ~/IPs/cdc_repo/*        # all five at once

Exit status is 0 only if every FAIL-level check passes, so it can gate a script.

What it enforces, and why:
  * no isIncludeFile      -- marks a file as a `include header; an XDC is never
                             included and a module source must be compiled, not
                             just put on the include path (IP_Flow 19-4296)
  * XDC in synthesis fg   -- if the constraints file is not shipped, the IP
                             carries none of the CDC exceptions and every
                             crossing is unconstrained while still looking green
  * FREQ_HZ               -- a freshly inferred clock has no parameters at all
                             (IP_Flow 19-11770)
  * ASSOCIATED_RESET      -- tells IPI which reset belongs to which clock
  * POLARITY=ACTIVE_LOW   -- every block uses `negedge rst_n`; ACTIVE_HIGH makes
                             IPI assert reset when it should release it
  * no POLARITY on clocks -- meaningless on a clock interface; usually a sign it
                             was added to the wrong interface by mistake
  * vendor / library      -- organisational only, so reported as WARN
"""
import glob
import os
import re
import sys

NS = r'(?:spirit|ipxact):'

# block -> {clock port: (FREQ_HZ, ASSOCIATED_RESET)}; ASSOCIATED_BUSIF where it applies
EXPECTED = {
    # sync_2ff and handshake_mcp are reusable in EITHER direction, so they
    # deliberately carry no FREQ_HZ -- a fixed one is non-overridable in IPI and
    # collides with the actual connected clock (BD 41-238). Same as the FIFOs.
    "sync_2ff":        {"clk":     (None, "rst_n",     None)},
    # Instantiated on ALL THREE domains, so no fixed FREQ_HZ (BD 41-238).
    "reset_sync":      {"clk":     (None, "arst_n",    None)},
    "pulse_sync":      {"src_clk": ("125000000", "src_rst_n", None),
                        "dst_clk": ("250000000", "dst_rst_n", None)},
    "async_fifo":      {"wr_clk":  ("156250000", "wr_rst_n",  None),
                        "rd_clk":  ("250000000", "rd_rst_n",  None)},
    "axis_async_fifo": {"s_clk":   ("156250000", "s_rst_n",   "S_AXIS"),
                        "m_clk":   ("250000000", "m_rst_n",   "M_AXIS")},
    "handshake_mcp":   {"src_clk": (None, "src_rst_n", None),
                        "dst_clk": (None, "dst_rst_n", None)},
    # The whole trading core packaged as one IP. Single-clock: every crossing
    # lives outside it, in the CDC blocks that surround it in the block design.
    "rtl_brain":       {"clk":     ("250000000", "rst_n", "m_axis:s_axis:s_axil")},
    # Non-CDC glue. Two clocks but no crossing inside; it exposes loose wires,
    # so ASSOCIATED_BUSIF is legitimately empty (IP_Flow 19-5661 is expected).
    "mc_glue":         {"clk_host": ("125000000", "rst_host_n", None),
                        "clk_core": ("250000000", "rst_core_n", None)},
}

# Blocks that must ship an always-apply <name>.xdc because they contain a
# crossing of their own. rtl_brain deliberately does not: it has no CDC inside,
# so it carries only its out-of-context clock definition.
NEEDS_ALWAYS_XDC = {"sync_2ff", "reset_sync", "pulse_sync", "async_fifo",
                    "axis_async_fifo", "handshake_mcp"}

# block -> every .sv that must ship inside the IP. Wrapper blocks instantiate a
# sub-module; ship only the wrapper and the IP will not elaborate downstream.
SOURCES = {
    "sync_2ff":        {"sync_2ff.sv"},
    "reset_sync":      {"reset_sync.sv"},
    "pulse_sync":      {"pulse_sync.sv", "sync_2ff.sv"},
    "async_fifo":      {"async_fifo.sv"},
    "axis_async_fifo": {"axis_async_fifo.sv", "async_fifo.sv"},
    "handshake_mcp":   {"handshake_mcp.sv", "sync_2ff.sv"},
    "rtl_brain":       {"tick_to_trade_top.sv", "itch_parser.sv", "order_book_m2.sv",
                        "risk_check.sv", "latency_counter.sv", "strategy_select.sv",
                        "strategy_imbalance.sv", "strategy_ofi.sv"},
    "mc_glue":         {"mc_glue.sv"},
}


def tag(text, name):
    m = re.search(rf'<{NS}{name}>([^<]*)</{NS}{name}>', text)
    return m.group(1) if m else None


def bus_interfaces(xml):
    """Yield (name, busType, {param: value}) for each bus interface."""
    for m in re.finditer(rf'<{NS}busInterface>(.*?)</{NS}busInterface>', xml, re.S):
        body = m.group(1)
        name = tag(body, "name")
        bt = re.search(rf'<{NS}busType[^>]*{NS}name="([^"]*)"', body)
        # Parse each <parameter> block separately: Vivado may insert other
        # elements (e.g. <description>) between <name> and <value>, so the two
        # cannot be matched as adjacent tags.
        params = {}
        for pm in re.finditer(rf'<{NS}parameter>(.*?)</{NS}parameter>', body, re.S):
            pb = pm.group(1)
            pn = re.search(rf'<{NS}name>([^<]*)</{NS}name>', pb)
            pv = re.search(rf'<{NS}value[^>]*>([^<]*)</{NS}value>', pb)
            if pn and pv:
                params[pn.group(1)] = pv.group(1)
        yield name, (bt.group(1) if bt else "?"), params


def files_with_include(xml):
    out = []
    for m in re.finditer(rf'<{NS}file>(.*?)</{NS}file>', xml, re.S):
        body = m.group(1)
        inc = tag(body, "isIncludeFile")
        if inc == "true":
            out.append(os.path.basename(tag(body, "name") or "?"))
    return out


def xdc_filesets(xml):
    """basename -> (set of fileSets containing it, set of its userFileType tags).

    Vivado records USED_IN as <userFileType>USED_IN_*</userFileType>. Observed
    behaviour: the tag is EXCLUSIVE, not additive -- a file carrying only
    USED_IN_out_of_context does not apply when the IP is instantiated, even
    though it still sits in the synthesis fileset. That is what stopped the
    packaged _ooc.xdc creating shadow clocks; it also silently disables an
    always-apply file if the tag lands on it by mistake.
    """
    out = {}
    for fs in re.finditer(rf'<{NS}fileSet>(.*?)</{NS}fileSet>', xml, re.S):
        body = fs.group(1)
        fsname = tag(body, "name") or "?"
        for fm in re.finditer(rf'<{NS}file>(.*?)</{NS}file>', body, re.S):
            fbody = fm.group(1)
            fname = tag(fbody, "name") or ""
            if not fname.endswith(".xdc"):
                continue
            tags = set(re.findall(rf'<{NS}userFileType>([^<]*)</{NS}userFileType>', fbody))
            base = os.path.basename(fname)
            fss, old = out.get(base, (set(), set()))
            out[base] = (fss | {fsname}, old | tags)
    return out


def check(path):
    if os.path.isdir(path):
        path = os.path.join(path, "component.xml")
    if not os.path.isfile(path):
        print(f"  [FAIL] no component.xml at {path}")
        return False

    xml = open(path, errors="ignore").read()
    name = tag(xml, "name") or "?"
    print(f"\n=== {name}  ({path})")

    fails, warns = [], []

    vendor, library = tag(xml, "vendor"), tag(xml, "library")
    if vendor != "hft":
        warns.append(f"vendor is {vendor!r}, expected 'hft'")
    want_lib = "cdc" if name in NEEDS_ALWAYS_XDC else "hft"
    if library != want_lib:
        warns.append(f"library is {library!r}, expected {want_lib!r}")
    desc = tag(xml, "description")
    if not desc or desc == f"{name}_v1_0":
        warns.append("description still the default (IP_Flow 19-11888)")

    inc = files_with_include(xml)
    if inc:
        fails.append(f"isIncludeFile=true on {', '.join(inc)} (IP_Flow 19-4296)")

    # The constraints come in two flavours and MUST NOT be interchanged:
    #   <name>_ooc.xdc  create_clock on the block's own ports. Valid only while
    #                   the block is the top of its own OOC run. If this reaches
    #                   the integrated design it creates shadow clocks that
    #                   compete with the real top-level ones -- that is what put
    #                   wr_clk/s_clk/src_clk into report_cdc instead of
    #                   clk_line/clk_core/clk_host, with Constraints 18-619 on
    #                   the way in and 18-513 on the port-based false path.
    #   <name>.xdc      ASYNC_REG + set_max_delay -datapath_only. Names no clock
    #                   and no port, so it stays valid in any hierarchy and must
    #                   apply everywhere -- it is what keeps the crossings
    #                   constrained once integrated.
    SYNTH_FS = "xilinx_anylanguagesynthesis_view_fileset"
    xdcs = xdc_filesets(xml)
    ooc_xdc, always_xdc = f"{name}_ooc.xdc", f"{name}.xdc"

    OOC_TAG = "USED_IN_out_of_context"

    # The _ooc file may be packaged, but ONLY if it is tagged out-of-context.
    # Untagged, its create_clock calls follow the IP into the integrated design
    # and shadow the real top-level clocks (that is how wr_clk/s_clk/src_clk
    # reached report_cdc instead of clk_line/clk_core/clk_host).
    if ooc_xdc in xdcs:
        fss, tags = xdcs[ooc_xdc]
        if OOC_TAG not in tags:
            fails.append(f"{ooc_xdc} is packaged WITHOUT {OOC_TAG}, so its create_clock "
                         "calls apply once the IP is instantiated and shadow the real "
                         "top-level clocks. Either tag it out-of-context only, or take "
                         "it out of constrs_1 and let Vivado generate the OOC clocks")

    # The always-apply file must NOT carry the out-of-context tag: that makes it
    # inert exactly where it is needed, and nothing in the reports says so.
    if always_xdc not in xdcs:
        if name in NEEDS_ALWAYS_XDC:
            fails.append(f"{always_xdc} not shipped -- once integrated the IP carries NO "
                         "CDC exceptions at all: no ASYNC_REG, no set_max_delay, and every "
                         "crossing is unconstrained while still looking green. The file is "
                         "usually already in the project: the missing step is Package IP -> "
                         "File Groups -> Merge changes from File Groups Wizard, then Re-Package")
    else:
        fss, tags = xdcs[always_xdc]
        if SYNTH_FS not in fss:
            fails.append(f"{always_xdc} is not in the synthesis fileset, so its CDC "
                         "exceptions never apply. Set USED_IN to include synthesis and "
                         "implementation")
        if OOC_TAG in tags:
            fails.append(f"{always_xdc} is tagged {OOC_TAG}, which restricts it to the "
                         "IP's own out-of-context run. Its ASYNC_REG and set_max_delay "
                         "then do NOTHING in the integrated design, and no report will "
                         "tell you -- the crossings just quietly go unbounded. Set "
                         "USED_IN to {synthesis implementation} and re-package")

    # The XDC is shipped by reference, so check what it actually contains: a
    # filter like {NAME =~ *wq_reg[0]*} is a Tcl character class, matches no
    # cells, and the exception silently applies to nothing.
    ip_root = os.path.dirname(os.path.abspath(path))
    for rel in re.findall(rf'<{NS}name>([^<]*\.xdc)</{NS}name>', xml):
        xdc = os.path.join(ip_root, rel)
        base = os.path.basename(rel)
        if not os.path.isfile(xdc):
            fails.append(f"{base} is referenced but missing on disk")
            continue
        body = open(xdc, errors="ignore").read()
        bad = re.findall(r'NAME\s*=~[^}]*?_reg\[0\]\*', body)
        if bad:
            fails.append(f"{base}: {len(bad)} filter(s) use an "
                         "unescaped _reg[0]* -- Tcl reads [0] as a character class, "
                         "so the constraint matches zero cells. Escape as _reg\\[0\\]*")
        # Content must match the flavour, whatever the filesets claim.
        if base == always_xdc:
            for stray in re.findall(r'^\s*(create_clock|set_clock_groups)\b', body, re.M):
                fails.append(f"{base} contains {stray} -- that is context-specific and "
                             f"belongs in {ooc_xdc}; here it will shadow the real clocks")
            if re.search(r'^\s*set_(false_path|max_delay).*get_ports', body, re.M):
                fails.append(f"{base} constrains a path from/to get_ports -- once "
                             "instantiated those are internal pins, not valid timing "
                             "startpoints (Constraints 18-513). Use get_cells instead")
        if base == ooc_xdc and not re.search(r'^\s*create_clock\b', body, re.M):
            warns.append(f"{base} defines no clock -- an OOC run will have none")

    shipped = {os.path.basename(f)
               for f in re.findall(rf'<{NS}name>([^<]*\.sv)</{NS}name>', xml)}
    for missing in sorted(SOURCES.get(name, set()) - shipped):
        fails.append(f"{missing} is not in the IP -- {name} instantiates it, so the "
                     "IP will not elaborate (add it in Add Sources, then re-package)")

    expect = EXPECTED.get(name)
    seen_clocks = set()
    summary = []
    for bname, btype, params in bus_interfaces(xml):
        if btype == "clock":
            seen_clocks.add(bname)
            rst = params.get("ASSOCIATED_RESET")
            want = expect[bname][1] if (expect and bname in expect) else None
            mark = "" if want is None else ("  <-- OK" if rst == want
                                            else f"  <-- WRONG, expected {want}")
            summary.append(f"  clock {bname:9} FREQ_HZ={params.get('FREQ_HZ','MISSING'):>10}"
                           f"  reset={rst or 'MISSING'}{mark}")
            if "POLARITY" in params:
                fails.append(f"clock {bname!r} has POLARITY -- meaningless on a clock; "
                             "it likely belongs on the reset interface")
            if "FREQ_HZ" not in params:
                # Deliberate for a direction-agnostic crossing block: a fixed
                # FREQ_HZ only validates in one orientation, so the same IP used
                # both ways fails IPI validation (BD 41-237/41-238). Omitting it
                # lets IPI propagate the real frequency from the connected clock.
                warns.append(f"clock {bname!r} has no FREQ_HZ (IP_Flow 19-11770) -- "
                             "correct if this block is used in both directions")
            if "ASSOCIATED_RESET" not in params:
                fails.append(f"clock {bname!r} has no ASSOCIATED_RESET")
            if expect and bname in expect:
                freq, rst, busif = expect[bname]
                if params.get("FREQ_HZ") not in (None, freq):
                    warns.append(f"clock {bname!r} FREQ_HZ={params['FREQ_HZ']}, "
                                 f"runbook says {freq}")
                if params.get("ASSOCIATED_RESET") not in (None, rst):
                    warns.append(f"clock {bname!r} ASSOCIATED_RESET="
                                 f"{params['ASSOCIATED_RESET']}, runbook says {rst}")
                # Vivado names the inferred interface from the port prefix, so the
                # case it uses (s_axis) differs from the convention (S_AXIS).
                # Either is correct -- compare case-insensitively.
                got_busif = (params.get("ASSOCIATED_BUSIF") or "").upper()
                if busif and got_busif != busif.upper():
                    fails.append(f"clock {bname!r} needs ASSOCIATED_BUSIF={busif} "
                                 f"(got {params.get('ASSOCIATED_BUSIF') or 'MISSING'})")
        elif btype == "reset":
            summary.append(f"  reset {bname:9} POLARITY={params.get('POLARITY','MISSING')}")
            if params.get("POLARITY") != "ACTIVE_LOW":
                fails.append(f"reset {bname!r} POLARITY={params.get('POLARITY')}, "
                             "expected ACTIVE_LOW")

    for line in summary:
        print(line)

    if expect:
        for missing in set(expect) - seen_clocks:
            fails.append(f"expected clock interface {missing!r} was not inferred")

    for f in fails:
        print(f"  [FAIL] {f}")
    for w in warns:
        print(f"  [warn] {w}")
    if not fails and not warns:
        print("  [OK  ] all checks passed")
    elif not fails:
        print("  [OK  ] no blocking problems (warnings are organisational)")
    return not fails


def main(argv):
    targets = []
    for a in argv:
        targets.extend(sorted(glob.glob(a)) or [a])
    if not targets:
        print(__doc__)
        return 2
    ok = all([check(t) for t in targets])
    print("\n" + ("ALL IPs PASS" if ok else "PROBLEMS FOUND -- see [FAIL] lines above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
