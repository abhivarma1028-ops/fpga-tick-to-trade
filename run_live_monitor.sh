#!/usr/bin/env bash
# One-command INTERACTIVE live trade monitor. Launches the control server
# (host/monitor_server.py), which serves the page AND accepts the on-page control
# buttons (start/stop/pause, taker<->maker, threshold, reset kill switch,
# add/remove symbol). Opens the page; Ctrl-C stops it.
#
# Usage:
#   ./run_live_monitor.sh                 # port 8000, 20 ticks/s
#   ./run_live_monitor.sh --port 8001 --rate 30
#
set -u
cd "$(dirname "$0")"

PORT=8000
# pull --port out of the args if given (so the URL matches)
for i in "$@"; do
  [ "${PREV:-}" = "--port" ] && PORT="$i"
  PREV="$i"
done
URL="http://localhost:${PORT}/live.html"

echo "starting interactive control server ..."
python host/monitor_server.py "$@" &
SRV=$!
trap 'echo; echo "stopping ..."; kill "$SRV" 2>/dev/null' EXIT INT TERM

sleep 1.5
echo
echo "============================================================"
echo "  LIVE CONTROL MONITOR:  ${URL}"
echo "  Controllable from the page: start/stop, taker<->maker,"
echo "  threshold, reset kill switch, add/remove symbol."
echo "  (Ctrl-C here to stop)"
echo "============================================================"
( command -v xdg-open >/dev/null && xdg-open "${URL}" ) 2>/dev/null \
  || ( command -v open >/dev/null && open "${URL}" ) 2>/dev/null || true

wait "$SRV"
