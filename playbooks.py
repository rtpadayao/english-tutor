"""Playbook scene cards (v4): markdown CONTENT, not a subsystem.

A playbook is a short scene card: roles, goal, the student's real script lines, key_phrases
for the drill stage, likely objections, success criteria. ONE card per session goes into the
roleplay system prompt (capped) — no playbook manager, no RAG. Cards live in playbooks/*.md.
"""
from pathlib import Path

PLAYBOOKS_DIR = Path(__file__).resolve().parent / "playbooks"
MAX_CARD_CHARS = 4000


def list_playbooks() -> list[dict]:
    out = []
    if PLAYBOOKS_DIR.exists():
        for p in sorted(PLAYBOOKS_DIR.glob("*.md")):
            title = p.stem.replace("-", " ")
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            out.append({"id": p.stem, "title": title})
    return out


def safe_path(playbook_id: str) -> Path | None:
    """Path-traversal-safe lookup: bare stem only, must exist."""
    if not playbook_id or "/" in playbook_id or "\\" in playbook_id or ".." in playbook_id:
        return None
    p = PLAYBOOKS_DIR / f"{playbook_id}.md"
    return p if p.exists() else None


def load_card(playbook_id: str) -> str | None:
    p = safe_path(playbook_id)
    if not p:
        return None
    return p.read_text(encoding="utf-8").strip()[:MAX_CARD_CHARS]


def drill_phrases(playbook_id: str | None, fallback: list[str]) -> tuple[list[str], str]:
    """Key phrases from the scene card, else the fallback list. Returns (phrases, source)."""
    card = load_card(playbook_id) if playbook_id else None
    if card:
        phrases, in_block = [], False
        for line in card.splitlines():
            stripped = line.strip()
            if stripped.startswith("key_phrases:"):
                in_block = True
                continue
            if in_block:
                if stripped.startswith("- "):
                    phrases.append(stripped[2:].strip().strip('"'))
                elif stripped:
                    break  # next top-level key — block ended
        phrases = [p for p in phrases if p][:5]
        if phrases:
            return phrases, f"playbook:{playbook_id}"
    return fallback[:5], "recent_corrections"
