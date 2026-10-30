"""Persistent mistake tracker (v3).

Every tutor turn and its corrections are logged to SQLite (data/tutor.db, gitignored —
private). The weekly digest aggregates tag counts with deterministic SQL — the LLM only
writes the advice and focus items, it never does the counting.

DB failures never break tutoring: log_turn swallows exceptions.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "tutor.db"

_DDL = """
CREATE TABLE IF NOT EXISTS turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  mode TEXT NOT NULL,
  student_text TEXT NOT NULL,
  reply TEXT NOT NULL,
  n_corrections INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  turn_id INTEGER NOT NULL REFERENCES turns(id),
  ts TEXT NOT NULL,
  original TEXT NOT NULL,
  corrected TEXT NOT NULL,
  tag TEXT NOT NULL,
  explanation TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_corrections_ts ON corrections(ts);
CREATE INDEX IF NOT EXISTS idx_corrections_tag ON corrections(tag);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)  # idempotent
    return conn


def log_turn(mode: str, student_text: str, reply_text: str, corrections: list[dict]) -> bool:
    """Persist one tutor turn. Returns False (never raises) if the DB write fails."""
    try:
        conn = _conn()
        try:
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cur = conn.execute(
                "INSERT INTO turns(ts, mode, student_text, reply, n_corrections) VALUES(?,?,?,?,?)",
                (ts, mode, student_text[:4000], reply_text[:4000], len(corrections)),
            )
            turn_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO corrections(turn_id, ts, original, corrected, tag, explanation)"
                " VALUES(?,?,?,?,?,?)",
                [
                    (turn_id, ts, c["original"][:500], c["corrected"][:500],
                     c["tag"], c["explanation"][:500])
                    for c in corrections
                ],
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as exc:
        print(f"[tracker] log failed: {exc}", flush=True)
        return False


def week_summary(days: int = 7) -> dict:
    """Deterministic aggregation: turns, total corrections, top tags with examples."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    conn = _conn()
    try:
        turns = conn.execute(
            "SELECT COUNT(*) AS n FROM turns WHERE ts >= ?", (since,)
        ).fetchone()["n"]
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM corrections WHERE ts >= ?", (since,)
        ).fetchone()["n"]
        rows = conn.execute(
            "SELECT tag, COUNT(*) AS n, GROUP_CONCAT(original, ' ;; ') AS examples"
            " FROM corrections WHERE ts >= ? GROUP BY tag ORDER BY n DESC LIMIT 4",
            (since,),
        ).fetchall()
    finally:
        conn.close()
    top = [
        {
            "tag": r["tag"],
            "count": r["n"],
            "examples": [e[:120] for e in (r["examples"] or "").split(" ;; ") if e][:3],
        }
        for r in rows
    ]
    return {
        "period": {"days": days, "since": since},
        "turns": turns,
        "corrections": total,
        "top_tags": top,
    }


def build_digest(days: int = 7) -> dict:
    """SQL counts + one LLM call for advice/focus. Empty period short-circuits (no LLM call)."""
    import tutor as tutor_mod  # local import: tutor has no tracker dependency

    summary = week_summary(days)
    if not summary["top_tags"]:
        summary["focus"] = [
            "No corrections logged in this period — have a few conversations, or run a drill."
        ]
        return summary

    lines = []
    for i, t in enumerate(summary["top_tags"], start=1):
        examples = " | ".join(f'"{e}"' for e in t["examples"])
        lines.append(f"Row {i} — {t['tag']} ({t['count']}x): {examples}")
    prompt = (
        "You are Roy's English tutor writing his weekly error digest.\n\n"
        f"Aggregated corrections for the last {days} days (SQL counts; tags are his recurring"
        " error types):\n" + "\n".join(lines) + "\n\n"
        "Write:\n"
        "- patterns: EXACTLY one entry per Row above (row = that row's number; do not invent"
        " rows). advice = ONE practical sentence on how to fix that row's error pattern,"
        " referencing one of its examples.\n"
        "- focus: 2-3 concrete practice actions for next week (specific: a drill to run, a"
        " phrase pattern to rehearse, or a client-call situation to practice)."
    )
    try:
        content = tutor_mod.ollama_chat(
            [
                {"role": "system",
                 "content": "You answer ONLY with the JSON object described by the schema."},
                {"role": "user", "content": prompt},
            ],
            tutor_mod.DIGEST_SCHEMA,
            num_predict=380,
            temperature=0.4,
        )
        parsed = json.loads(content)
    except Exception as exc:
        print(f"[tracker] digest LLM failed: {exc}", flush=True)
        summary["focus"] = [
            "(Digest advice unavailable this time — the counts above are still accurate.)"
        ]
        return summary

    for p in parsed.get("patterns") or []:
        if not isinstance(p, dict):
            continue
        try:
            idx = int(p.get("row")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(summary["top_tags"]):
            summary["top_tags"][idx]["advice"] = str(p.get("advice") or "").strip()
    focus = [str(f).strip() for f in (parsed.get("focus") or []) if str(f).strip()][:3]
    summary["focus"] = focus or ["Keep running drills on the tags above."]
    return summary
