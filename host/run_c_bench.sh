#!/usr/bin/env bash
# Build + run the compiled-C software latency baseline, and verify the C port
# matches the Python golden (phase2_golden) on the same stream.
#   usage: host/run_c_bench.sh [N_MSGS] [SEED] [REPEATS]
set -euo pipefail
cd "$(dirname "$0")/.."

N="${1:-3000}"; SEED="${2:-1}"; REPEATS="${3:-50}"
STREAM=/tmp/itch_stream_${N}_${SEED}.bin

echo "== generate identical ITCH stream (n=$N seed=$SEED) =="
python3 -c "import sys;sys.path.insert(0,'sim');import replay_gen;open('$STREAM','wb').write(replay_gen.build_synthetic($N,$SEED))"

echo "== Python golden stats (reference) =="
python3 -c "
import sys;sys.path.insert(0,'sim')
import replay_gen, phase2_golden
st=open('$STREAM','rb').read()
d,r,s=phase2_golden.run(st)
print(f'  [python] parsed={s[\"messages\"]} accepted={s[\"accepted\"]} final_position={s[\"final_position\"]}')
"

echo "== build C (-O2) =="
gcc -O2 -o /tmp/sw_latency_bench host/sw_latency_bench.c

echo "== run C baseline =="
/tmp/sw_latency_bench "$STREAM" "$REPEATS"
echo
echo "NOTE: the C [verify] line MUST match the [python] line above (same computation)."
