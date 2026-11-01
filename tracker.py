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
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  correction_id INTEGER NOT NULL REFERENCES corrections(id),
  ts TEXT NOT NULL,
  recall TEXT NOT NULL CHECK(recall IN ('hit','miss'))
);
CREATE INDEX IF NOT EXISTS idx_reviews_correction ON reviews(correction_id);
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


def review_queue(limit: int = 3) -> list[dict]:
    """Corrections due for re-test: aged >= 2 days, not retired (last 2 reviews both hits).

    Feeds the warm-up stage; the same reviews data feeds the digest recall-rate.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.original, c.corrected, c.tag, c.explanation, c.ts,
                   (SELECT COUNT(*) FROM reviews r WHERE r.correction_id = c.id
                      AND r.recall = 'miss') AS misses,
                   (SELECT MAX(r2.ts) FROM reviews r2 WHERE r2.correction_id = c.id) AS last_review
            FROM corrections c
            WHERE c.ts <= :cutoff
              AND (SELECT GROUP_CONCAT(recall) FROM (
                     SELECT recall FROM reviews WHERE correction_id = c.id
                     ORDER BY id DESC LIMIT 2
                   )) IS NOT 'hit,hit'
            ORDER BY (SELECT MAX(r2.ts) FROM reviews r2 WHERE r2.correction_id = c.id) IS NULL DESC,
                     misses DESC, c.ts ASC
            LIMIT :limit
            """,
            {"cutoff": cutoff, "limit": limit},
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def record_review(correction_id: int, recall: str) -> bool:
    """Log one re-test result. Returns False (never raises) on bad input or DB failure."""
    if recall not in ("hit", "miss"):
        return False
    try:
        conn = _conn()
        try:
            if not conn.execute("SELECT 1 FROM corrections WHERE id = ?", (correction_id,)).fetchone():
                return False
            conn.execute(
                "INSERT INTO reviews(correction_id, ts, recall) VALUES(?,?,?)",
                (correction_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), recall),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as exc:
        print(f"[tracker] review log failed: {exc}", flush=True)
        return False


def recent_corrected(days: int = 7, limit: int = 5) -> list[str]:
    """Corrected phrases from recent turns — the drill stage's fallback phrase source."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT corrected FROM corrections WHERE ts >= ? ORDER BY id DESC LIMIT ?",
            (since, limit),
        ).fetchall()
    finally:
        conn.close()
    return [r["corrected"] for r in rows]


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
        rv = conn.execute(
            "SELECT SUM(recall = 'hit') AS hits, COUNT(*) AS total"
            " FROM reviews WHERE ts >= ?",
            (since,),
        ).fetchone()
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
        "reviews": {"hits": int(rv["hits"] or 0), "total": int(rv["total"] or 0)},
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
    # v5 hardening: qwen's pretty-printed digest JSON truncates at num_predict=380
    # (probe: 4/5 runs cut mid-string at ~1.5k chars) and occasionally returns rows
    # that don't map. Two layers: a cap that fits the real output size, and the v3
    # retry pattern — the demand moves into the user turn at lower temperature, so
    # latency is paid only while the contract is violated. Counts above are SQL-
    # derived and never depend on the LLM.
    row_hint = (
        "\n\nIMPORTANT: patterns must contain EXACTLY one entry per Row listed above — "
        f"row is that row's integer number (1..{len(summary['top_tags'])}) and advice is "
        "one non-empty practical sentence referencing one of its examples."
    )
    last_focus: list[str] | None = None
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            content = tutor_mod.ollama_chat(
                [
                    {"role": "system",
                     "content": "You answer ONLY with the JSON object described by the schema."},
                    {"role": "user", "content": prompt + (row_hint if attempt > 1 else "")},
                ],
                tutor_mod.DIGEST_SCHEMA,
                num_predict=700,
                temperature=0.4 if attempt == 1 else 0.3,
            )
            parsed = json.loads(content)
        except Exception as exc:
            print(f"[tracker] digest LLM attempt {attempt}/{attempts} failed: {exc}", flush=True)
            continue

        focus = [str(f).strip() for f in (parsed.get("focus") or []) if str(f).strip()][:3]
        if focus:
            last_focus = focus

        mapped = 0
        for p in parsed.get("patterns") or []:
            if not isinstance(p, dict):
                continue
            try:
                idx = int(p.get("row")) - 1
            except (TypeError, ValueError):
                continue
            advice = str(p.get("advice") or "").strip()
            if 0 <= idx < len(summary["top_tags"]) and advice:
                summary["top_tags"][idx]["advice"] = advice
                mapped += 1
        summary["focus"] = focus or ["Keep running drills on the tags above."]
        if mapped:
            return summary
        print(
            f"[tracker] digest attempt {attempt}/{attempts}: "
            f"0/{len(summary['top_tags'])} rows mapped",
            flush=True,
        )

    # every attempt failed — counts stay accurate; keep the best focus we saw
    summary["focus"] = last_focus or [
        "(Digest advice unavailable this time — the counts above are still accurate.)"
    ]
    return summary
