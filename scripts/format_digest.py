#!/usr/bin/env python3
"""Format the English-tutor weekly digest JSON into an Obsidian markdown note.

Usage: python3 format_digest.py <digest.json> <output.md>
Called by weekly_digest.sh (Sundays 19:00 via Hermes cron).
"""
import json
import sys
from datetime import date


def main() -> None:
    digest = json.load(open(sys.argv[1], encoding="utf-8"))
    out_path = sys.argv[2]

    lines = [
        "# English tutor — weekly digest",
        "",
        f"Date: {date.today().isoformat()}",
        "Source: english-tutor /api/digest (SQL counts + tutor-written advice)",
        "",
    ]
    period = digest.get("period", {})
    lines.append(f"Period: last {period.get('days', '?')} days (since {str(period.get('since', '?'))[:10]})")
    lines.append(f"Conversation turns: {digest.get('turns', 0)}")
    lines.append(f"Corrections logged: {digest.get('corrections', 0)}")
    reviews = digest.get("reviews") or {}
    if reviews.get("total"):
        lines.append(f"Warm-up recall: {reviews['hits']}/{reviews['total']} re-tests hit")
    lines.append("")

    top = digest.get("top_tags") or []
    if top:
        lines.append("## Patterns")
        for t in top:
            line = f"- {t['tag']} x{t['count']}"
            if t.get("advice"):
                line += f" — {t['advice']}"
            lines.append(line)
        lines.append("")

    lines.append("## Focus next week")
    for f in digest.get("focus") or []:
        lines.append(f"- {f}")
    lines.append("")

    open(out_path, "w", encoding="utf-8").write("\n".join(lines))
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
