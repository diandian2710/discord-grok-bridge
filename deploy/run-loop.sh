#!/usr/bin/env bash
# Keep a development-mode bridge running on hosts without systemd.
# Usage: deploy/run-loop.sh [state-dir]   (default: work/bridge-local)
# Start detached:  setsid nohup deploy/run-loop.sh >/dev/null 2>&1 </dev/null &
# Stop:            pkill -f run-loop.sh; pkill -f "bridge run --config"
set -u
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
state=${1:-work/bridge-local}
log="$state/bridge.log"
echo $$ > "$state/run-loop.pid"
while true; do
  echo "$(date -Is) starting bridge" >> "$log"
  uv run --frozen --no-editable bridge run --config "$state/service.json" >> "$log" 2>&1
  code=$?
  echo "$(date -Is) bridge exited code=$code; restarting in 5s" >> "$log"
  sleep 5
done
