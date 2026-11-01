# english-tutor

Local, voice-first English tutor for professional / client English practice.
Thin v1 built from scratch (design + repo audit: see Obsidian note
`2026-10-30_english-ai-tutor_plan.md` in the vault).

Stack: faster-whisper (small, int8, CPU) + Ollama qwen3.5:9b + edge-tts + FastAPI + one static page.
Design rule: ASR and TTS run on CPU; the 8 GB VRAM is reserved for the LLM.

## Run

    python3 -m venv ~/venvs/english-tutor
    ~/venvs/english-tutor/bin/pip install -r requirements.txt
    cp .env.example .env
    scripts/start.sh    # pid file + logs/tutor.log + health check; stop: scripts/stop.sh
    # or manually: ~/venvs/english-tutor/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8770
    # open http://127.0.0.1:8770

Prereqs: Ollama running on :11434 with `qwen3.5:9b` pulled; internet for edge-tts.

## Endpoints

    GET  /                tutor page
    GET  /api/health      component status
    POST /api/transcribe  multipart file=<audio> -> {text, elapsed_s}
    POST /api/chat        {text, mode, history} -> {reply, corrections, rephrase, elapsed_s}
    POST /api/tts         {text}                -> audio/mpeg

    corrections: [{original, corrected, tag, explanation}] — tags:
                 grammar|tense|preposition|article|word-choice|register|spelling|other
    rephrase:    {professional, variants[], usage} — filled in rephrase mode only

## Mistake tracker (v3)

Every tutor turn + its corrections are logged to data/tutor.db (SQLite, gitignored — private).
The Digest button aggregates the last 7 days with SQL (deterministic tag counts — the LLM
never counts), then the LLM writes per-pattern advice and 2-3 focus items. DB failures
never block tutoring.

## Playbooks + roleplay (v4)

playbooks/*.md are short roleplay scene cards. Pick
"Roleplay" in the mode dropdown, choose a card, and the tutor plays the counterpart while you
deliver your real script lines — with the usual gentle corrections, all logged to the tracker.

## Daily session flow (v4)

The Session button walks: Warm-up (re-say corrections aged 2+ days; two consecutive hits
retire an item) -> Voice drill (listen to a phrase, repeat it, scored by text similarity —
a proxy, not phoneme accuracy) -> Main set (3-5 conversation/roleplay turns) -> summary.
Every stage is skippable; no streaks, no guilt.

## Weekly digest cron (v4)

scripts/weekly_digest.sh (Sundays 19:00 via Hermes cron) ensures the server is up, fetches
/api/digest, and writes a markdown note to ~/Documents/Obsidian-Vault/english-tutor/.

## Smoke test (server must be running)

    ~/venvs/english-tutor/bin/python scripts/smoke_test.py

File-driven loop: tts -> transcribe -> chat -> tts, with latency report.

## Modes

- free      conversation; tutor answers first, then max 3 gentle corrections (panel, not voice)
- rephrase  "how do I say X" -> professional phrasing + variants + usage; session history kept
- drill     strict: EVERY error, tagged; corrections also spoken after the reply

Spoken-audio rule: free = reply only; rephrase = reply + professional phrasing;
drill = reply + up to 3 spoken corrections. Gentle mode never interrupts by voice.

Corrections come back as structured JSON (Ollama structured outputs, schema-validated
server-side with a plain-text fallback).

## License

Personal project — no license file yet. Add one before ever sharing the repo
(the no-license finding on the audited reference repos is the cautionary tale).
