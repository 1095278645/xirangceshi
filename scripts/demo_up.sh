#!/usr/bin/env bash
# One-click demo launch for Linux/macOS.
# Usage: bash scripts/demo_up.sh [--force-seed] [--skip-warm] [--port 8000]
set -euo pipefail

FORCE_SEED=0
SKIP_WARM=0
PORT=8000
while [ $# -gt 0 ]; do
  case "$1" in
    --force-seed) FORCE_SEED=1 ;;
    --skip-warm)  SKIP_WARM=1 ;;
    --port)       PORT="$2"; shift ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac
  shift
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/server"
DB="$SERVER/data/ai_shopkeeper.db"

echo "== 1/6 check python =="
python --version

echo "== 2/6 demo data =="
if [ "$FORCE_SEED" = "1" ] || [ ! -f "$DB" ]; then
  ( cd "$SERVER" && { [ "$FORCE_SEED" = "1" ] && python ../scripts/seed_demo_data.py --force || python ../scripts/seed_demo_data.py; } )
else
  echo "  + demo db exists (use --force-seed to reseed): $DB"
fi

echo "== 3/6 start backend on 0.0.0.0:$PORT =="
if curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  echo "  + backend already running (reusing)"
else
  ( cd "$SERVER" && nohup python -m uvicorn main:app --host 0.0.0.0 --port "$PORT" \
      > data/uvicorn.out.log 2> data/uvicorn.err.log & echo $! > data/uvicorn.pid )
  for _ in $(seq 1 30); do
    sleep 1
    if curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then break; fi
  done
  echo "  + backend started (stop with: kill \$(cat server/data/uvicorn.pid))"
fi

echo "== 4/6 LAN address for the phone =="
if command -v hostname >/dev/null 2>&1; then
  for ip in $(hostname -I 2>/dev/null || true); do echo "  + http://${ip}:${PORT}"; done
fi
echo "  Set this URL in the app settings (More -> backend address)."

if [ "$SKIP_WARM" = "0" ]; then
  echo "== 5/6 prewarm AI cache =="
  ( cd "$SERVER" && python ../scripts/prewarm_cache.py )
else
  echo "== 5/6 prewarm skipped =="
fi

echo "== 6/6 pre-demo self-check =="
( cd "$SERVER" && python ../scripts/mp_demo_check.py )
echo "Done. Keep the backend running during the demo."
