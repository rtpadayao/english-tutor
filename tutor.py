"""Tutor brain v2: persona + modes -> structured JSON via Ollama structured outputs.

Response contract (enforced by Ollama's format=schema, defended again in parse()):
  reply        natural conversational answer — this is what TTS reads
  corrections  list of {original, corrected, tag, explanation}
  rephrase     {professional, variants[], usage} — filled in rephrase mode, empty otherwise

Correction tags are a fixed enum so the UI can chip them and v3 can aggregate them in the
mistake tracker. History is client-side (last 8 turns per request); server-side memory
(Honcho + SQLite) is v3 — see the plan note.
"""
import json
import os
import time
import urllib.request

TAGS = ["grammar", "tense", "preposition", "article", "word-choice", "register", "spelling", "other"]

PERSONA = """\
You are a patient, encouraging English tutor in a one-on-one session. You answer ONLY with
the JSON object described by the schema (keys: reply, corrections, rephrase).

Student profile:
- Roy, a data/analytics consultant from the Philippines (first language Filipino/Tagalog).
- Level: intermediate to advanced. Goal: client-ready professional English for consulting
  calls, presentations, and remote job interviews.
- Target: American English — natural, but professional.

Field rules:
- reply: respond naturally to what he said, like a good conversation partner would — answer
  him, ask one short follow-up question, or give exactly what he asked for. Keep it SHORT
  (2-6 sentences): it is read aloud by TTS. Never mention corrections inside reply and never
  use the word "Corrections" there.
- corrections: ONLY real errors or clearly awkward phrasing he actually produced. Each item:
  original = the exact phrase he said (short quote), corrected = the better version,
  tag = one of grammar|tense|preposition|article|word-choice|register|spelling|other,
  explanation = ONE short sentence with the reason.
  * free mode: at most 3 items; if there are none, use an empty list.
  * drill mode: list EVERY error, including small ones (articles, prepositions, tense,
    word choice); still answer the content first, briefly, in reply.
  * rephrase mode: usually an empty list unless he also asked to be corrected.
- rephrase: when he asks "how do I say X", professional = the best professional phrasing,
  variants = 1-2 natural alternatives, usage = one line on when to use it. In all other
  modes use professional="", variants=[], usage="".

Tone: encouragement over pedantry. Explanations in simple English; a short Tagalog gloss
only if he asks for one.
"""

# The server knows the mode; the model must be TOLD (v2 regression fix — without this the
# model guesses from the message text and rephrase turns can come back as plain conversation).
MODE_ADDONS = {
    "free": "\nACTIVE MODE: free conversation.",
    "rephrase": (
        "\nACTIVE MODE: rephrase — the student is asking HOW TO SAY something in professional "
        "English. The rephrase object is REQUIRED for this turn: professional must be non-empty, "
        "with 1-2 variants and a usage line. Keep reply to one short supportive sentence."
    ),
    "drill": (
        "\nACTIVE MODE: drill — a practice exercise, not conversation. List EVERY error, "
        "including small ones (articles, prepositions, tense, word choice), in corrections."
    ),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "corrections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "original": {"type": "string"},
                    "corrected": {"type": "string"},
                    "tag": {"type": "string", "enum": TAGS},
                    "explanation": {"type": "string"},
                },
                "required": ["original", "corrected", "tag", "explanation"],
            },
        },
        "rephrase": {
            "type": "object",
            "properties": {
                "professional": {"type": "string"},
                "variants": {"type": "array", "items": {"type": "string"}},
                "usage": {"type": "string"},
            },
            "required": ["professional", "variants", "usage"],
        },
    },
    "required": ["reply", "corrections", "rephrase"],
}


def _cfg(key: str, default: str) -> str:
    return os.environ.get(key, default)


def ollama_ok() -> bool:
    try:
        with urllib.request.urlopen(_cfg("OLLAMA_URL", "http://localhost:11434") + "/api/version", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def empty_rephrase() -> dict:
    return {"professional": "", "variants": [], "usage": ""}


def parse(content: str) -> dict:
    """Validate the model's JSON; degrade gracefully to a plain reply if it ever breaks."""
    try:
        obj = json.loads(content)
        if not isinstance(obj, dict):
            raise ValueError("not an object")
    except (json.JSONDecodeError, ValueError):
        print(f"[tutor] non-JSON reply ({len(content)} chars) — falling back to plain text", flush=True)
        if not content.strip():
            raise RuntimeError("Ollama returned an empty reply")
        return {"reply": content.strip(), "corrections": [], "rephrase": empty_rephrase()}

    reply_text = str(obj.get("reply") or "").strip()
    if not reply_text:
        raise RuntimeError("Ollama returned an empty reply")

    corrections = []
    for item in obj.get("corrections") or []:
        if not isinstance(item, dict):
            continue
        original = str(item.get("original") or "").strip()
        corrected = str(item.get("corrected") or "").strip()
        if not original or not corrected:
            continue
        tag = str(item.get("tag") or "other").strip().lower()
        if tag not in TAGS:
            tag = "other"
        corrections.append({
            "original": original,
            "corrected": corrected,
            "tag": tag,
            "explanation": str(item.get("explanation") or "").strip(),
        })

    raw = obj.get("rephrase")
    if not isinstance(raw, dict):
        raw = {}
    rephrase = {
        "professional": str(raw.get("professional") or "").strip(),
        "variants": [str(v).strip() for v in (raw.get("variants") or []) if str(v).strip()],
        "usage": str(raw.get("usage") or "").strip(),
    }
    return {"reply": reply_text, "corrections": corrections, "rephrase": rephrase}


def reply(text: str, mode: str = "free", history: list[dict] | None = None) -> tuple[dict, float]:
    """Return ({reply, corrections, rephrase}, elapsed_seconds) for one tutor turn."""
    messages = [{"role": "system", "content": PERSONA + MODE_ADDONS.get(mode, "")}]
    for turn in (history or [])[-8:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": str(turn["content"])[:2000]})
    messages.append({"role": "user", "content": text})

    payload = {
        "model": _cfg("OLLAMA_MODEL", "qwen3.5:9b"),
        "messages": messages,
        "stream": False,
        # qwen3.5 is a thinking model: without this, tokens go to message.thinking
        # and content comes back empty (matches Honcho's no-THINKING setup for this model).
        "think": False,
        # v2: structured outputs — Ollama constrains generation to SCHEMA.
        "format": SCHEMA,
        "keep_alive": "30m",
        # 420 (was 320): JSON keys/brackets add token overhead.
        "options": {"temperature": 0.6, "num_predict": 420},
    }
    req = urllib.request.Request(
        _cfg("OLLAMA_URL", "http://localhost:11434") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.load(resp)
    content = (data.get("message") or {}).get("content", "").strip()
    return parse(content), time.perf_counter() - start
