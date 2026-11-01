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
  tag = one of grammar|tense|preposition|article|word-choice|register|spelling|other —
  prefer the MOST SPECIFIC tag that fits (tense for verb forms, article for a/an/the,
  preposition for in/on/at/to, word-choice for vocabulary); use "other" ONLY when none fit.
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
    "roleplay": (
        "\nACTIVE MODE: roleplay — a SCENE CARD follows at the end of this system message. "
        "Stay IN CHARACTER as the counterpart it describes and run the practice scenario: "
        "reply = your in-character turn (short, spoken aloud; react, object, or move the "
        "scene forward as that persona would). corrections = as in free mode (max 3, real "
        "errors only). rephrase = empty unless he asks how to say something."
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

# v3: weekly digest — the LLM writes advice/focus; tag COUNTS come from SQL, not the model.
# Patterns are merged by ROW NUMBER, not by the LLM echoing tag names (models mangle keys).
DIGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "patterns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "row": {"type": "integer"},
                    "advice": {"type": "string"},
                },
                "required": ["row", "advice"],
            },
        },
        "focus": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["patterns", "focus"],
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


def ollama_chat(messages: list[dict], schema: dict, num_predict: int = 420,
                temperature: float = 0.6, timeout: int = 180) -> str:
    """One schema-constrained Ollama call; returns raw content (caller parses/validates)."""
    payload = {
        "model": _cfg("OLLAMA_MODEL", "qwen3.5:9b"),
        "messages": messages,
        "stream": False,
        # qwen3.5 is a thinking model: without this, tokens go to message.thinking
        # and content comes back empty (matches Honcho's no-THINKING setup for this model).
        "think": False,
        # structured outputs — Ollama constrains generation to the schema.
        "format": schema,
        "keep_alive": "30m",
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    req = urllib.request.Request(
        _cfg("OLLAMA_URL", "http://localhost:11434") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    content = (data.get("message") or {}).get("content", "").strip()
    if not content:
        raise RuntimeError("Ollama returned an empty reply")
    return content


REPHRASE_HINT = (
    "(This is a REPHRASE request. Your JSON MUST include a non-empty rephrase.professional — "
    "the professional English version of the message above — plus 1-2 variants and a usage line.)"
)
DRILL_HINT = (
    "(This is a DRILL exercise. Analyze the message above for grammar, tense, article, "
    "preposition, and word-choice errors. Your JSON MUST list EVERY error you find in "
    "corrections, each with a tag and a one-line reason. Only return an empty corrections "
    "list if the text is already flawless English.)"
)


def _retry_once(messages: list[dict], text: str, hint: str) -> dict:
    """One corrective retry: the demand moves into the user turn, temperature drops."""
    retry_messages = messages[:-1] + [{"role": "user", "content": text + "\n\n" + hint}]
    return parse(ollama_chat(retry_messages, SCHEMA, num_predict=420, temperature=0.3))


def reply(text: str, mode: str = "free", history: list[dict] | None = None,
          playbook_card: str | None = None) -> tuple[dict, float]:
    """Return ({reply, corrections, rephrase}, elapsed_seconds) for one tutor turn."""
    system = PERSONA + MODE_ADDONS.get(mode, "")
    if mode == "roleplay" and playbook_card:
        system += "\n\nSCENE CARD:\n" + playbook_card
    messages = [{"role": "system", "content": system}]
    for turn in (history or [])[-8:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": str(turn["content"])[:2000]})
    messages.append({"role": "user", "content": text})

    start = time.perf_counter()
    try:
        result = parse(ollama_chat(messages, SCHEMA, num_predict=420))
    except Exception as exc:
        # Transient model-server hiccups (empty reply, connection blip, momentary
        # VRAM pressure) must not surface as 500s — one retry at lower temperature
        # converts the common single-shot failure into latency. A second genuine
        # failure still raises (the 500 is then real).
        print(f"[tutor] chat attempt 1 failed ({exc}) — retrying once", flush=True)
        result = parse(ollama_chat(messages, SCHEMA, num_predict=420, temperature=0.3))
    # Hard mode contracts are enforced by RETRY, not by prompt alone — a 9B model at
    # temperature 0.6 occasionally ignores mode instructions. The retry moves the demand
    # into the user turn at lower temperature and costs latency only when violated.
    if mode == "rephrase" and not result["rephrase"]["professional"]:
        print("[tutor] rephrase contract violated — retrying once", flush=True)
        result = _retry_once(messages, text, REPHRASE_HINT)
    if mode == "drill" and not result["corrections"]:
        # A clean drill input pays one wasted retry (a few seconds); an erroneous one gets fixed.
        print("[tutor] drill returned no corrections — retrying once", flush=True)
        result = _retry_once(messages, text, DRILL_HINT)
    return result, time.perf_counter() - start
