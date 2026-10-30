"""Tutor brain: persona + modes -> Ollama chat.

History is client-side in v1 (last 8 turns sent with each request).
Server-side memory (Honcho + SQLite mistake tracker) is v3 — see the plan note.
"""
import json
import os
import time
import urllib.request

PERSONA = """\
You are a patient, encouraging English tutor in a one-on-one session.

Student profile:
- Roy, a data/analytics consultant from the Philippines (first language Filipino/Tagalog).
- Level: intermediate to advanced. Goal: client-ready professional English for consulting
  calls, presentations, and remote job interviews.
- Target: American English — natural, but professional.

How to respond:
1. First respond naturally to what he said, like a good conversation partner would:
   answer him, ask one short follow-up question, or give exactly what he asked for.
2. Then, ONLY if he made real errors or clearly awkward phrasing, add a section whose
   first line is exactly "Corrections:" and list AT MOST 3 items, each on its own line
   as: said "..." -> say "..." — one short reason. Never invent errors; if there are
   none, skip the section entirely.
3. For a "how do I say X" request, lead with the professional phrasing, give 1-2 natural
   variants, and one line on when to use each. Skip the Corrections section unless he
   also asked to be corrected.
4. Keep the conversational part SHORT: 2-6 sentences. Replies are read aloud by TTS.
5. Explain in simple English. Use a short Tagalog gloss only if he asks for one.
6. Encouragement over pedantry. Never correct grammar in the middle of a friendly
   exchange beyond the limit above.
"""

MODE_ADDONS = {
    "free": "",
    "rephrase": "\nRight now the student is asking how to say something. Lead with the phrasing.\n",
    "drill": (
        "\nDRILL MODE: this is a practice exercise, not conversation. Correct EVERY error, "
        "including small ones (articles, prepositions, tense, word choice), each as "
        'said "..." -> say "..." — short reason. Still answer the content first, briefly.\n'
    ),
}


def _cfg(key: str, default: str) -> str:
    return os.environ.get(key, default)


def ollama_ok() -> bool:
    try:
        with urllib.request.urlopen(_cfg("OLLAMA_URL", "http://localhost:11434") + "/api/version", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def reply(text: str, mode: str = "free", history: list[dict] | None = None) -> tuple[str, float]:
    """Return (reply_text, elapsed_seconds) for one tutor turn."""
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
        "keep_alive": "30m",
        "options": {"temperature": 0.6, "num_predict": 320},
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
    if not content:
        raise RuntimeError("Ollama returned an empty reply")
    return content, time.perf_counter() - start
