#!/usr/bin/env bash
# Run the API and the dashboard together. Ctrl-C stops both.
#
#   ./scripts/dev.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ -d "$ROOT/.venv" ] || { echo "No .venv — run ./scripts/setup.sh first" >&2; exit 1; }

# shellcheck disable=SC1091
[ -f "$ROOT/.env" ] && set -a && . "$ROOT/.env" && set +a

API_PORT="${API_PORT:-8000}"
API_HOST="${API_HOST:-0.0.0.0}"

pids=()
cleanup() {
  echo
  echo "Stopping..."
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting API on :$API_PORT"
# --reload-dir pins the watcher to source. Without it WatchFiles also sees
# workspaces/ and artifacts/, which the pipeline writes during a run: cloning
# the customer repo would trip a reload and kill the in-flight pipeline.
"$ROOT/.venv/bin/uvicorn" app.main:app \
  --app-dir "$ROOT/apps/api" --reload \
  --reload-dir "$ROOT/apps/api" --reload-dir "$ROOT/packages" \
  --host "$API_HOST" --port "$API_PORT" &
pids+=($!)

# Wait for /health before starting the UI, so the dashboard's first fetch does
# not land on a socket that is not listening yet.
echo -n "Waiting for API"
for _ in $(seq 1 40); do
  if curl -fsS "http://localhost:$API_PORT/health" >/dev/null 2>&1; then
    echo " ok"
    break
  fi
  echo -n "."
  sleep 0.5
done

echo "Starting dashboard on :3000"
(cd "$ROOT/apps/ui" && npm run dev) &
pids+=($!)

echo
echo "  API       http://localhost:$API_PORT"
echo "  Dashboard http://localhost:3000"
echo
wait
