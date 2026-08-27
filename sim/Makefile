# cocotb Makefile
# Usage:
#   make                                                        # itch_parser (default)
#   make TOPLEVEL=order_book_top    MODULE=tb_order_book
#   make TOPLEVEL=order_book_m2     MODULE=tb_order_book_m2
#   make TOPLEVEL=latency_counter   MODULE=tb_latency_counter
#   make TOPLEVEL=tick_to_trade_top MODULE=tb_tick_to_trade
#   make SIM=verilator                                          # quick smoke test

SIM            ?= questa
TOPLEVEL_LANG  ?= verilog
TOPLEVEL       ?= itch_parser
MODULE         ?= tb_itch_parser

# --- Pure-Python golden checks (no simulator needed) ----------------------
# strat-equiv : assert host/strategy_sw.py == golden/strategy_imbalance.py
#               (live software path vs the RTL-validated reference).
# py-golden   : print the phase-2 golden decision trace.
.PHONY: strat-equiv py-golden regression
strat-equiv:
	python tb_strategy_sw_equiv.py
	python tb_strategy_equiv_all.py
py-golden:
	python phase2_golden.py
# whole-project pure-Python regression (equivalence + HFT logic + maker + smokes)
regression:
	cd .. && ./run_all_tests.sh

# --- CDC IP-library regression (multi-clock RTL, Verilator) ----------------
# Each block is a standalone toplevel compiled from just the cdc/ sources
# (CDC=1 overrides VERILOG_SOURCES below). Runs both clock directions where
# the TB defines them. Green here = data integrity across unrelated clocks.
.PHONY: cdc-regression
CDC_BLOCKS := sync_2ff:tb_sync_2ff reset_sync:tb_reset_sync pulse_sync:tb_pulse_sync \
              async_fifo:tb_async_fifo axis_async_fifo:tb_axis_async_fifo \
              handshake_mcp:tb_handshake_mcp
cdc-regression:
	@for b in $(CDC_BLOCKS); do \
	  top=$${b%%:*}; mod=$${b##*:}; \
	  echo "==== CDC $$top / $$mod ===="; \
	  $(MAKE) -s sim CDC=1 SIM=verilator TOPLEVEL=$$top MODULE=$$mod \
	    SIM_BUILD=sim_build_cdc/$$top || exit 1; \
	done
	@echo "==== CDC regression PASSED ===="

# --- Phase 2: the 3-clock-domain top ---------------------------------------
# Same ITCH stimulus as tb_tick_to_trade, but bytes go in on clk_line, the core
# runs on clk_core and decisions/CSR come out on clk_host -- so a pass proves
# the CDC blocks work in situ, not just in their own unit tests.
.PHONY: mc-test
mc-test:
	@echo "==== tick_to_trade_mc_top (line 156.25 / core 250 / host 125 MHz) ===="
	@$(MAKE) -s sim SIM=verilator TOPLEVEL=tick_to_trade_mc_top \
	   MODULE=tb_tick_to_trade_mc SIM_BUILD=sim_build_mc \
	   EXTRA_ARGS="--trace --trace-fst"
	@echo "==== multi-clock top PASSED ===="

RTL_DIR        := $(abspath ../rtl)

VERILOG_SOURCES  = $(RTL_DIR)/itch_parser.sv
VERILOG_SOURCES += $(RTL_DIR)/order_book_top.sv
VERILOG_SOURCES += $(RTL_DIR)/order_book_m2.sv
VERILOG_SOURCES += $(RTL_DIR)/multi_symbol_book.sv
VERILOG_SOURCES += $(RTL_DIR)/strategy_imbalance.sv
VERILOG_SOURCES += $(RTL_DIR)/strategy_ofi.sv
VERILOG_SOURCES += $(RTL_DIR)/strategy_select.sv
VERILOG_SOURCES += $(RTL_DIR)/pipe_divu.sv
VERILOG_SOURCES += $(RTL_DIR)/strategy_market_maker.sv
VERILOG_SOURCES += $(RTL_DIR)/strategy_ofi_maker.sv
VERILOG_SOURCES += $(RTL_DIR)/market_maker_top.sv
VERILOG_SOURCES += $(RTL_DIR)/risk_check.sv
VERILOG_SOURCES += $(RTL_DIR)/latency_counter.sv
VERILOG_SOURCES += $(RTL_DIR)/tick_to_trade_top.sv
VERILOG_SOURCES += $(RTL_DIR)/multi_symbol_top.sv
# CDC primitives + the 3-clock-domain wrapper (Phase 2). Harmless for the
# single-clock testbenches -- unused modules are simply not elaborated.
VERILOG_SOURCES += $(RTL_DIR)/cdc/sync_2ff.sv
VERILOG_SOURCES += $(RTL_DIR)/cdc/reset_sync.sv
VERILOG_SOURCES += $(RTL_DIR)/cdc/pulse_sync.sv
VERILOG_SOURCES += $(RTL_DIR)/cdc/async_fifo.sv
VERILOG_SOURCES += $(RTL_DIR)/cdc/handshake_mcp.sv
VERILOG_SOURCES += $(RTL_DIR)/cdc/axis_async_fifo.sv
VERILOG_SOURCES += $(RTL_DIR)/tick_to_trade_mc_top.sv
VERILOG_SOURCES += $(abspath ../f1/cl_tick_to_trade_core.sv)

# SystemVerilog Assertions, bound to the DUTs. Questa only — the Verilator
# smoke path and Vivado synthesis never see this file.
ifeq ($(SIM),questa)
    VERILOG_SOURCES += $(abspath pipeline_sva.sv)
endif

# Questa: point to your installation
QUESTA_DIR     ?= /home/abhishek/questasim2021

# Global timescale — avoids per-file `timescale directives which break
# under Questa 2021.2 -mfcu multi-file compilation
COMPILE_ARGS += -timescale 1ns/1ps

# Extra compile flags
ifeq ($(SIM),questa)
    SIM_ARGS += -suppress 3009    # 'module not used' warnings during M1
    # 12036: bind targets a module not instantiated under this toplevel
    # (the SVA file binds to all DUTs; single-module tests only have one) — benign.
    SIM_ARGS += -suppress 12036
endif

ifeq ($(SIM),verilator)
    EXTRA_ARGS += --trace --trace-fst
endif

# CDC block tests compile ONLY the cdc/ sources (self-contained crossing IPs).
# Usage: make CDC=1 SIM=verilator TOPLEVEL=async_fifo MODULE=tb_async_fifo
ifdef CDC
VERILOG_SOURCES = $(RTL_DIR)/cdc/sync_2ff.sv \
                  $(RTL_DIR)/cdc/reset_sync.sv \
                  $(RTL_DIR)/cdc/pulse_sync.sv \
                  $(RTL_DIR)/cdc/async_fifo.sv \
                  $(RTL_DIR)/cdc/handshake_mcp.sv \
                  $(RTL_DIR)/cdc/axis_async_fifo.sv
endif

COCOTB_RESOLVE_X = ZEROS

include $(shell cocotb-config --makefiles)/Makefile.sim
