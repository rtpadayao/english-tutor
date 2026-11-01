#!/usr/bin/env bash
# stop.sh — stop the English Tutor started by start.sh.
# Kills the whole process group so uvicorn (and any worker children) die too.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/tutor.pid"

PORT=8770
if [ -f "$ROOT/.env" ]; then
  ENV_PORT="$(grep -E '^TUTOR_PORT=' "$ROOT/.env" | tail -1 | cut -d= -f2 | tr -d '[:space:]')"
  [ -n "$ENV_PORT" ] && PORT="$ENV_PORT"
fi

if [ ! -f "$PID_FILE" ]; then
  echo "NOT RUNNING (no pid file)"
  if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
    echo "NOTE: something is still listening on port $PORT — it was not started by start.sh."
    echo "Find it with: ss -ltnp | grep :$PORT"
  fi
  exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  # Kill the process group (-$PID) so uvicorn children die too.
  kill -TERM -"$PID" 2>/dev/null || kill -TERM "$PID" 2>/dev/null
  # Wait up to 10s for a clean exit, then force.
  for _ in $(seq 1 10); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 1
  done
  if kill -0 "$PID" 2>/dev/null; then
    kill -KILL -"$PID" 2>/dev/null || kill -KILL "$PID" 2>/dev/null || true
    echo "Sent SIGKILL to process group $PID"
  else
    echo "Stopped (pid $PID)"
  fi
else
  echo "STALE pid $PID (process gone) — cleaning pid file"
fi
rm -f "$PID_FILE"
