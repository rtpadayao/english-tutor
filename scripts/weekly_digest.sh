#!/usr/bin/env bash
# Weekly English-tutor digest -> Obsidian note.
# Scheduled Sundays 19:00 via Hermes cron (see plan note Section 14, AC19).
# Ensures the tutor server is up, fetches /api/digest, writes a dated note to the vault.
set -u

PORT=8770
BASE=/home/roy/repos/english-tutor
VENV=/home/roy/venvs/english-tutor
DIGEST_URL="http://127.0.0.1:${PORT}/api/digest"
VDIR="/home/roy/Documents/Obsidian-Vault/english-tutor"

mkdir -p "$VDIR"

# Ensure the server is running; start it if not (loopback-only uvicorn).
if ! curl -s --max-time 3 "${DIGEST_URL%/api/digest}/api/health" >/dev/null 2>&1; then
  echo "server down — starting"
  (cd "$BASE" && nohup "$VENV/bin/python" -m uvicorn server:app --host 127.0.0.1 --port "$PORT" \
    >> "$BASE/data/server.log" 2>&1 &)
  sleep 8
fi

json=$(curl -s --max-time 180 "$DIGEST_URL")
if [ -z "$json" ]; then
  echo "digest fetch failed" >&2
  exit 1
fi

echo "$json" > "$BASE/data/last_digest.json"
"$VENV/bin/python" "$BASE/scripts/format_digest.py" \
  "$BASE/data/last_digest.json" \
  "$VDIR/$(date +%F)_english-tutor_weekly-digest.md"
