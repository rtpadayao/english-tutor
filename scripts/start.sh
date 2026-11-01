#!/usr/bin/env bash
# start.sh — start the English Tutor (FastAPI/uvicorn, loopback only) with pid+log.
# Launches in a new process group so stop.sh can kill the whole tree.
# Prereqs: venv at ~/venvs/english-tutor; Ollama on :11434 with qwen3.5:9b.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$HOME/venvs/english-tutor/bin/python"
PID_FILE="$ROOT/tutor.pid"
LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/tutor.log"

# Port: TUTOR_PORT from .env (server.py reads it too), default 8770.
PORT=8770
if [ -f "$ROOT/.env" ]; then
  ENV_PORT="$(grep -E '^TUTOR_PORT=' "$ROOT/.env" | tail -1 | cut -d= -f2 | tr -d '[:space:]')"
  [ -n "$ENV_PORT" ] && PORT="$ENV_PORT"
fi

mkdir -p "$LOG_DIR"

if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE")"
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "ALREADY RUNNING (pid $OLD_PID) on http://127.0.0.1:$PORT"
    exit 0
  fi
  echo "Removing stale pid file for $OLD_PID"
  rm -f "$PID_FILE"
fi

if [ ! -x "$PY" ]; then
  echo "ERROR: venv python not found at $PY"
  echo "Setup: python3 -m venv ~/venvs/english-tutor && ~/venvs/english-tutor/bin/pip install -r $ROOT/requirements.txt"
  exit 1
fi

# Guard against an instance started outside this script (e.g. via README command).
if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  echo "ERROR: port $PORT already in use, but no pid file — was the server started manually?"
  echo "Find it with: ss -ltnp | grep :$PORT   (then stop it, or run scripts/stop.sh)"
  exit 1
fi

if ! curl -s --max-time 2 http://localhost:11434 >/dev/null 2>&1; then
  echo "WARNING: Ollama not reachable on :11434 — chat endpoints will fail until it is up."
fi

cd "$ROOT"
# setsid detaches into a new session/process group; the recorded pid is the
# group leader. server.py loads .env itself; ASR/TTS are CPU, LLM goes to Ollama.
setsid nohup "$PY" -m uvicorn server:app --host 127.0.0.1 --port "$PORT" >"$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

# Wait for /api/health (fast: whisper is lazy-loaded on first transcribe).
for _ in $(seq 1 30); do
  if curl -sf --max-time 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "STARTED (pid $(cat "$PID_FILE")) on http://127.0.0.1:$PORT"
    echo "Health: curl http://127.0.0.1:$PORT/api/health"
    echo "Logs:   $LOG_FILE"
    exit 0
  fi
  sleep 1
done

echo "ERROR: server did not become healthy within 30s. Last log lines:"
tail -n 20 "$LOG_FILE" || true
exit 1
