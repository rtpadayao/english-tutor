"""End-to-end file-driven smoke test: health -> tts(student line) -> transcribe -> chat -> tts.

Run with the project venv while the server is up:
    ~/venvs/english-tutor/bin/python scripts/smoke_test.py

Mirrors AC2-AC5 of the plan note and prints the latency numbers to paste into Section 5.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

URL = "http://127.0.0.1:8770"
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

SAMPLE = (
    "Hi, I'd like to practice explaining a data pipeline to a client. "
    "Can we role-play that I am presenting my approach for their reporting migration?"
)


async def make_student_audio() -> Path:
    from tts import synthesize

    audio = await synthesize(SAMPLE)
    path = DATA / "smoke_student.mp3"
    path.write_bytes(audio)
    return path


def main() -> None:
    # 1. health
    r = requests.get(f"{URL}/api/health", timeout=10)
    r.raise_for_status()
    print("health:", json.dumps(r.json()))

    # 2. student-side audio (what a recorded turn would be)
    sample = asyncio.run(make_student_audio())
    print(f"student audio: {sample.name} {sample.stat().st_size} bytes")

    # 3. transcribe (first call includes model load)
    t0 = time.perf_counter()
    r = requests.post(
        f"{URL}/api/transcribe",
        files={"file": (sample.name, sample.read_bytes(), "audio/mpeg")},
        timeout=300,
    )
    r.raise_for_status()
    body = r.json()
    print(f"transcribe: server={body['elapsed_s']}s roundtrip={time.perf_counter() - t0:.2f}s")
    print(f"  heard: {body['text']!r}")
    assert body["text"], "empty transcription"

    # 4. tutor turn (first chat includes Ollama model load if evicted)
    t0 = time.perf_counter()
    r = requests.post(f"{URL}/api/chat", json={"text": body["text"], "mode": "free"}, timeout=300)
    r.raise_for_status()
    body = r.json()
    print(f"chat: server={body['elapsed_s']}s roundtrip={time.perf_counter() - t0:.2f}s")
    print("--- tutor reply ---")
    print(body["reply"])
    assert body["reply"], "empty reply"

    # 5. voice for the reply
    t0 = time.perf_counter()
    r = requests.post(f"{URL}/api/tts", json={"text": body["reply"]}, timeout=120)
    r.raise_for_status()
    out = DATA / "smoke_reply.mp3"
    out.write_bytes(r.content)
    print(f"tts: roundtrip={time.perf_counter() - t0:.2f}s -> {out} {len(r.content)} bytes")
    assert len(r.content) > 1000, "suspiciously small mp3"

    print("SMOKE OK")


if __name__ == "__main__":
    main()
