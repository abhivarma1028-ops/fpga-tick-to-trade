# =============================================================================
# build_mc_bd.tcl — create the mc_bd project and generate the whole block design
#
#   cd ~/Projects/HFT
#   vivado -mode batch -source ip/build_mc_bd.tcl
#
# Creates /home/abhishek/IPs/projects/mc_bd with the IP repository
# registered, then places and wires the 10 blocks:
#
#   u_core      rtl_brain            the trading logic          (clk_core)
#   u_glue      mc_glue              config FSM + sticky flags  (host+core)
#   u_ingress   axis_async_fifo      ITCH bytes  line -> core
#   u_egress    axis_async_fifo      decisions   core -> host
#   u_axil_cc   axi_clock_converter  AXI-Lite    host <-> core
#   u_halt      sync_2ff             halt        host -> core
#   u_dec_ovf   sync_2ff             sticky flag core -> host
#   u_risk_ovf  sync_2ff             sticky flag core -> host
#   u_cfg       handshake_mcp        strat_sel   host -> core
#   u_risk_evt  handshake_mcp        risk event  core -> host
#
# The wiring is taken from rtl/tick_to_trade_mc_top.sv, which passes
# tb_tick_to_trade_mc 4/4 -- except the AXI-Lite path, which uses a Xilinx
# axi_clock_converter here because rtl_brain exposes s_axil as a single AXI-MM
# interface and its individual pins are not reachable in IP Integrator.
# =============================================================================

set PROJ    mc_bd
set PROJ_DIR "/home/abhishek/IPs/projects/mc_bd"
set BD      cdc_brain_top
set PART    xcu200-fsgd2104-2-e
set HFT     "/home/abhishek/Projects/HFT"

# All seven IPs live in one directory now, so a single repository covers them.
set REPOS [list "/home/abhishek/IPs/IP for HFT"]

# ---------------------------------------------------------------- project
file mkdir $PROJ_DIR
create_project -force $PROJ $PROJ_DIR -part $PART

set_property ip_repo_paths $REPOS [current_project]
update_ip_catalog -rebuild

puts "\n==== IP catalog check ===="
foreach v {hft:hft:rtl_brain:1.0 hft:hft:mc_glue:1.0 hft:cdc:axis_async_fifo:1.0
           hft:cdc:sync_2ff:1.0 hft:cdc:handshake_mcp:1.0 hft:cdc:reset_sync:1.0} {
    if {[llength [get_ipdefs -quiet $v]]} {
        puts "  found   $v"
    } else {
        error "NOT FOUND in catalog: $v -- check the repository paths"
    }
}
set CC [lindex [lsort [get_ipdefs -quiet xilinx.com:ip:axi_clock_converter:*]] end]
if {$CC eq ""} { error "axi_clock_converter not found in the Xilinx catalog" }
puts "  found   $CC"

# ---------------------------------------------------------------- block design
create_bd_design $BD
current_bd_design $BD

proc cell {name vlnv {props {}}} {
    set c [create_bd_cell -type ip -vlnv $vlnv $name]
    if {[llength $props]} { set_property -dict $props $c }
    return $c
}

cell u_core      hft:hft:rtl_brain:1.0
cell u_glue      hft:hft:mc_glue:1.0          {CONFIG.CFG_W 32}
cell u_ingress   hft:cdc:axis_async_fifo:1.0  {CONFIG.TDATA_W 8  CONFIG.DEPTH 512}
cell u_egress    hft:cdc:axis_async_fifo:1.0  {CONFIG.TDATA_W 72 CONFIG.DEPTH 64}
cell u_halt      hft:cdc:sync_2ff:1.0         {CONFIG.INIT_VAL 1}
cell u_dec_ovf   hft:cdc:sync_2ff:1.0
cell u_risk_ovf  hft:cdc:sync_2ff:1.0
cell u_cfg       hft:cdc:handshake_mcp:1.0    {CONFIG.WIDTH 32}
cell u_risk_evt  hft:cdc:handshake_mcp:1.0    {CONFIG.WIDTH 3}
# One reset synchronizer per domain: async assert, synchronous release. Without
# these every reset endpoint reports [CDC-7] Asynchronous reset unknown CDC
# circuitry -- 4375 of them plus 441 CDC-1, ~91% of all criticals.
cell u_rst_line  hft:cdc:reset_sync:1.0       {CONFIG.STAGES 3}
cell u_rst_core  hft:cdc:reset_sync:1.0       {CONFIG.STAGES 3}
cell u_rst_host  hft:cdc:reset_sync:1.0       {CONFIG.STAGES 3}
cell u_axil_cc   $CC {CONFIG.PROTOCOL AXI4LITE CONFIG.DATA_WIDTH 32 CONFIG.ADDR_WIDTH 9 CONFIG.ID_WIDTH 0}

# ---------------------------------------------------------------- ports
proc clkport {name period_ns} {
    # -freq_hz must be given at creation; setting CONFIG.FREQ_HZ afterwards
    # leaves the port frequency unset (BD 5-670).
    set hz [expr {int(1.0e9 / $period_ns)}]
    return [create_bd_port -dir I -type clk -freq_hz $hz $name]
}
clkport clk_line 6.400
clkport clk_core 4.000
clkport clk_host 8.000

foreach r {rst_line_n rst_core_n rst_host_n} {
    set p [create_bd_port -dir I -type rst $r]
    set_property CONFIG.POLARITY ACTIVE_LOW $p
}

create_bd_port -dir I halt
create_bd_port -dir I -from 1 -to 0 strat_sel
create_bd_port -dir O risk_reject
create_bd_port -dir O -from 2 -to 0 risk_reason
create_bd_port -dir O decision_overflow
create_bd_port -dir O risk_evt_overflow

create_bd_intf_port -mode Slave  -vlnv xilinx.com:interface:axis_rtl:1.0  s_axis
create_bd_intf_port -mode Master -vlnv xilinx.com:interface:axis_rtl:1.0  m_axis
create_bd_intf_port -mode Slave  -vlnv xilinx.com:interface:aximm_rtl:1.0 s_axil
# An external aximm port defaults to full AXI4; the converter slave is AXI4LITE,
# and mismatched protocols refuse to connect (BD 41-1285).
set_property -dict [list CONFIG.PROTOCOL AXI4LITE CONFIG.DATA_WIDTH 32 \
                        CONFIG.ADDR_WIDTH 9 CONFIG.FREQ_HZ 125000000] \
             [get_bd_intf_ports s_axil]

# ---------------------------------------------------------------- clocks
proc netclk {port pins} {
    foreach p $pins { connect_bd_net [get_bd_ports $port] [get_bd_pins $p] }
}
netclk clk_line {u_ingress/s_clk u_rst_line/clk}
netclk clk_core {u_core/clk u_glue/clk_core u_ingress/m_clk u_egress/s_clk
                 u_halt/clk u_cfg/dst_clk u_risk_evt/src_clk u_axil_cc/m_axi_aclk
                 u_rst_core/clk}
netclk clk_host {u_glue/clk_host u_egress/m_clk u_dec_ovf/clk u_risk_ovf/clk
                 u_cfg/src_clk u_risk_evt/dst_clk u_axil_cc/s_axi_aclk
                 u_rst_host/clk}

# The raw external resets feed ONLY the synchronizers; every block in a domain
# takes that domain's SYNCHRONIZED reset. Never share one across domains -- that
# would reintroduce the problem they exist to solve.
netclk rst_line_n {u_rst_line/arst_n}
netclk rst_core_n {u_rst_core/arst_n}
netclk rst_host_n {u_rst_host/arst_n}

proc netpin {src pins} {
    foreach p $pins { connect_bd_net [get_bd_pins $src] [get_bd_pins $p] }
}
netpin u_rst_line/rst_n {u_ingress/s_rst_n}
netpin u_rst_core/rst_n {u_core/rst_n u_glue/rst_core_n u_ingress/m_rst_n u_egress/s_rst_n
                         u_halt/rst_n u_cfg/dst_rst_n u_risk_evt/src_rst_n u_axil_cc/m_axi_aresetn}
netpin u_rst_host/rst_n {u_glue/rst_host_n u_egress/m_rst_n u_dec_ovf/rst_n u_risk_ovf/rst_n
                         u_cfg/src_rst_n u_risk_evt/dst_rst_n u_axil_cc/s_axi_aresetn}

# ---------------------------------------------------------------- datapath
connect_bd_intf_net [get_bd_intf_ports s_axis]        [get_bd_intf_pins u_ingress/s_axis]
connect_bd_intf_net [get_bd_intf_pins u_ingress/m_axis] [get_bd_intf_pins u_core/s_axis]
connect_bd_intf_net [get_bd_intf_pins u_core/m_axis]    [get_bd_intf_pins u_egress/s_axis]
connect_bd_intf_net [get_bd_intf_pins u_egress/m_axis]  [get_bd_intf_ports m_axis]
connect_bd_intf_net [get_bd_intf_ports s_axil]          [get_bd_intf_pins u_axil_cc/S_AXI]
connect_bd_intf_net [get_bd_intf_pins u_axil_cc/M_AXI]  [get_bd_intf_pins u_core/s_axil]

# ---------------------------------------------------------------- control / status
proc n {a b} { connect_bd_net [get_bd_pins $a] [get_bd_pins $b] }
proc np {port pin} { connect_bd_net [get_bd_ports $port] [get_bd_pins $pin] }

np halt      u_halt/d
n  u_halt/q  u_core/halt

np strat_sel u_glue/strat_sel
n  u_glue/cfg_data      u_cfg/src_data
n  u_glue/cfg_valid     u_cfg/src_valid
n  u_cfg/src_ready      u_glue/cfg_ready
n  u_cfg/dst_data       u_glue/cfg_word_core
n  u_glue/strat_sel_core u_core/strat_sel
# u_cfg/dst_valid is deliberately left unconnected: the config is level-held,
# so the arrival pulse is not needed.

n  u_core/risk_reject     u_glue/risk_reject_core
n  u_core/risk_reason     u_risk_evt/src_data
n  u_glue/risk_evt_valid  u_risk_evt/src_valid
n  u_risk_evt/src_ready   u_glue/risk_evt_ready
connect_bd_net [get_bd_pins u_risk_evt/dst_valid] [get_bd_ports risk_reject]
connect_bd_net [get_bd_pins u_risk_evt/dst_data]  [get_bd_ports risk_reason]

# Taps for the overflow detector. Connecting one member of an interface net
# REMOVES that pin from the interface connection (BD 41-1306) -- so tvalid would
# stop reaching u_egress and the decision path would silently break. Wire all
# three endpoints explicitly instead; the interface net still carries tdata/tlast.
connect_bd_net [get_bd_pins u_core/m_axis_tvalid] \
               [get_bd_pins u_egress/s_axis_tvalid] [get_bd_pins u_glue/dec_valid]
connect_bd_net [get_bd_pins u_egress/s_axis_tready] \
               [get_bd_pins u_core/m_axis_tready]   [get_bd_pins u_glue/dec_accept]

n u_glue/decision_overflow_core u_dec_ovf/d
n u_glue/risk_evt_overflow_core u_risk_ovf/d
connect_bd_net [get_bd_pins u_dec_ovf/q]  [get_bd_ports decision_overflow]
connect_bd_net [get_bd_pins u_risk_ovf/q] [get_bd_ports risk_evt_overflow]

# ---------------------------------------------------------------- finish
# The core's register block must be mapped into the external AXI-Lite space,
# otherwise validation reports the segment unassigned (BD 41-1356).
assign_bd_address -quiet
regenerate_bd_layout
save_bd_design
validate_bd_design

make_wrapper -files [get_files $PROJ_DIR/$PROJ.srcs/sources_1/bd/$BD/$BD.bd] -top
add_files -norecurse $PROJ_DIR/$PROJ.gen/sources_1/bd/$BD/hdl/${BD}_wrapper.v
set_property top ${BD}_wrapper [current_fileset]
update_compile_order -fileset sources_1

add_files -fileset constrs_1 -norecurse [list $HFT/rtl/xdc/mc_bd_top.xdc]

puts "\n================ block design built ================"
puts "cells: [llength [get_bd_cells]]"
foreach c [get_bd_cells] { puts "   [get_property NAME $c]" }
puts "project: $PROJ_DIR/$PROJ.xpr"
puts "===================================================="
