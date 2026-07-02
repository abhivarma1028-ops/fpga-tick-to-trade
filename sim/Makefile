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
py-golden:
	python phase2_golden.py
# whole-project pure-Python regression (equivalence + HFT logic + maker + smokes)
regression:
	cd .. && ./run_all_tests.sh

RTL_DIR        := $(abspath ../rtl)

VERILOG_SOURCES  = $(RTL_DIR)/itch_parser.sv
VERILOG_SOURCES += $(RTL_DIR)/order_book_top.sv
VERILOG_SOURCES += $(RTL_DIR)/order_book_m2.sv
VERILOG_SOURCES += $(RTL_DIR)/multi_symbol_book.sv
VERILOG_SOURCES += $(RTL_DIR)/strategy_imbalance.sv
VERILOG_SOURCES += $(RTL_DIR)/pipe_divu.sv
VERILOG_SOURCES += $(RTL_DIR)/strategy_market_maker.sv
VERILOG_SOURCES += $(RTL_DIR)/market_maker_top.sv
VERILOG_SOURCES += $(RTL_DIR)/risk_check.sv
VERILOG_SOURCES += $(RTL_DIR)/latency_counter.sv
VERILOG_SOURCES += $(RTL_DIR)/tick_to_trade_top.sv
VERILOG_SOURCES += $(RTL_DIR)/multi_symbol_top.sv
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

COCOTB_RESOLVE_X = ZEROS

include $(shell cocotb-config --makefiles)/Makefile.sim
