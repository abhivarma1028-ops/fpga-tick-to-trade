#!/usr/bin/env bash
# One-command regression: runs every pure-Python test/check in the project and
# reports pass/fail. No simulator (Questa/Verilator) needed — the cocotb RTL
# testbenches are run separately via sim/Makefile (they need a simulator).
#
# Usage:  ./run_all_tests.sh          (exit 0 = all passed, 1 = a failure)
set -u
cd "$(dirname "$0")"

PASS=0; FAIL=0; FAILED=()

run() {
    local name="$1"; shift
    echo "=============================================================="
    echo ">> $name"
    echo "   \$ $*"
    if ( "$@" ); then
        echo "   RESULT: PASS"; PASS=$((PASS+1))
    else
        echo "   RESULT: FAIL"; FAIL=$((FAIL+1)); FAILED+=("$name")
    fi
    echo ""
}

# --- correctness tests (assert / exit-code gated) ---
run "strategy_sw == golden/RTL equivalence" bash -c "cd sim  && python tb_strategy_sw_equiv.py"
run "HFT decision logic (portfolio + gauntlet)" bash -c "cd host && python tb_hft_logic.py"
run "market maker quoting"                       bash -c "cd host && python tb_market_maker.py"

# --- smoke runs (must complete without error; not pass/fail assertions) ---
run "phase-2 golden trace (smoke)"    bash -c "cd sim  && python phase2_golden.py 300 1 >/dev/null"
run "backtest taker vs maker (smoke)" bash -c "cd host && python backtest.py --n 500 --seed 1 >/dev/null"
run "signal alpha analysis (smoke)"   bash -c "cd host && python alpha_analysis.py --n 500 --seeds 2 >/dev/null"

echo "=============================================================="
echo "REGRESSION SUMMARY:  $PASS passed, $FAIL failed"
if [ "$FAIL" -ne 0 ]; then
    printf '  FAILED: %s\n' "${FAILED[@]}"
    exit 1
fi
echo "  ALL GREEN"
echo ""
echo "note: cocotb RTL testbenches (need Questa/Verilator) are separate:"
echo "  cd sim && make SIM=questa TOPLEVEL=<top> MODULE=<tb>   (see sim/Makefile)"
