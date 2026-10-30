"""End-to-end smoke test v2: health -> tts(student line) -> transcribe -> chat x3 -> tts.

Run with the project venv while the server is up:
    ~/venvs/english-tutor/bin/python scripts/smoke_test.py

Mirrors AC2-AC5 (v1) and AC7-AC9 (v2) of the plan note; prints latency numbers for Section 5/12.
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
DRILL_BAD = (
    "Yesterday I go to office and meet my client, he is very happy "
    "about the report we finish last week."
)
REPHRASE_TAG = "Hindi ko maipaliwanag nang maayos yung iniisip ko."
REQUIRED_KEYS = {"original", "corrected", "tag", "explanation"}


async def make_student_audio() -> Path:
    from tts import synthesize

    audio = await synthesize(SAMPLE)
    path = DATA / "smoke_student.mp3"
    path.write_bytes(audio)
    return path


def chat(text: str, mode: str) -> dict:
    t0 = time.perf_counter()
    r = requests.post(
        f"{URL}/api/chat",
        json={"text": text, "mode": mode},
        timeout=300,
    )
    r.raise_for_status()
    body = r.json()
    body["_roundtrip"] = time.perf_counter() - t0
    # v2 contract: every chat response carries the three structured keys
    for key in ("reply", "corrections", "rephrase"):
        assert key in body, f"missing key in chat response: {key}"
    return body


async def main() -> None:
    # 1. health
    r = requests.get(f"{URL}/api/health", timeout=10)
    r.raise_for_status()
    print("health:", json.dumps(r.json()))

    # 2. student-side audio (what a recorded turn would be)
    sample = await make_student_audio()
    print(f"student audio: {sample.name} {sample.stat().st_size} bytes")

    # 3. transcribe (first call includes model load)
    t0 = time.perf_counter()
    r = requests.post(
        f"{URL}/api/transcribe",
        files={"file": (sample.name, sample.read_bytes(), "audio/mpeg")},
        timeout=300,
    )
    r.raise_for_status()
    heard = r.json()
    print(f"transcribe: server={heard['elapsed_s']}s roundtrip={time.perf_counter() - t0:.2f}s")
    print(f"  heard: {heard['text']!r}")
    assert heard["text"], "empty transcription"

    # 4. free-mode tutor turn (structured)
    free = chat(heard["text"], "free")
    print(f"chat(free): server={free['elapsed_s']}s roundtrip={free['_roundtrip']:.2f}s "
          f"corrections={len(free['corrections'])}")
    print("  reply:", free["reply"])
    for c in free["corrections"]:
        print(f"  [{c['tag']}] {c['original']!r} -> {c['corrected']!r} ({c['explanation']})")

    # 5. drill mode: deliberately erroneous input must yield multiple tagged corrections
    drill = chat(DRILL_BAD, "drill")
    print(f"chat(drill): server={drill['elapsed_s']}s roundtrip={drill['_roundtrip']:.2f}s "
          f"corrections={len(drill['corrections'])}")
    for c in drill["corrections"]:
        print(f"  [{c['tag']}] {c['original']!r} -> {c['corrected']!r} ({c['explanation']})")
    assert len(drill["corrections"]) >= 2, "drill mode should catch multiple errors"
    for c in drill["corrections"]:
        assert REQUIRED_KEYS <= set(c), f"correction missing keys: {c}"
        assert c["tag"], "empty tag"

    # 6. rephrase mode: professional phrasing + variants + usage
    reph = chat(REPHRASE_TAG, "rephrase")
    rp = reph["rephrase"]
    print(f"chat(rephrase): server={reph['elapsed_s']}s roundtrip={reph['_roundtrip']:.2f}s")
    print(f"  professional: {rp['professional']!r}")
    print(f"  variants: {rp['variants']}")
    print(f"  usage: {rp['usage']!r}")
    assert rp["professional"], "rephrase.professional empty"
    assert rp["variants"], "rephrase.variants empty"

    # 7. voice for the reply
    t0 = time.perf_counter()
    r = requests.post(f"{URL}/api/tts", json={"text": free["reply"]}, timeout=120)
    r.raise_for_status()
    out = DATA / "smoke_reply.mp3"
    out.write_bytes(r.content)
    print(f"tts: roundtrip={time.perf_counter() - t0:.2f}s -> {out} {len(r.content)} bytes")
    assert len(r.content) > 1000, "suspiciously small mp3"

    print("SMOKE OK")


if __name__ == "__main__":
    asyncio.run(main())
