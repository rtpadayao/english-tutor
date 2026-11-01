"""Digest advice hardening: fake-LLM unit test (no Ollama needed, deterministic).

Covers the two failure classes seen in the 2026-11-01 flake probe:
  1. JSON truncation at the old num_predict cap  -> parse fail -> retry
  2. rows that don't map (out-of-range integers) -> zero advice -> retry
and the never-succeeds path: graceful focus, SQL counts untouched.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import tracker  # noqa: E402
import tutor  # noqa: E402

GOOD = json.dumps({
    "patterns": [
        {"row": 1, "advice": "Watch verb tense in past-tense stories."},
        {"row": 2, "advice": "Say 'also integrate it' instead of 'integrate it also'."},
    ],
    "focus": ["Drill past-tense stories daily."],
})
BAD_ROWS = json.dumps({
    "patterns": [{"row": 9, "advice": "x"}, {"row": 0, "advice": "y"}],
    "focus": ["Drill tenses."],
})
TRUNCATED = '{"patterns": [{"row": 1, "advice": "Wa'  # cut mid-string, like the probe saw


def run_with(fake, expect_attempts: int) -> dict:
    calls = {"n": 0}

    def fake_wrapper(messages, schema, num_predict=420, temperature=0.6, timeout=180):
        calls["n"] += 1
        return fake(calls["n"])

    tutor.ollama_chat = fake_wrapper  # tracker imports the module object at call time
    digest = tracker.build_digest(7)
    assert calls["n"] == expect_attempts, f"expected {expect_attempts} LLM calls, got {calls['n']}"
    return digest


# 1. truncated, then bad rows, then good -> advice lands on attempt 3
digest = run_with(lambda n: [TRUNCATED, BAD_ROWS, GOOD][n - 1], expect_attempts=3)
assert digest["top_tags"][0]["advice"] == "Watch verb tense in past-tense stories.", digest["top_tags"][0]
assert digest["focus"] == ["Drill past-tense stories daily."]
print("test_digest_retry: OK (truncated -> bad rows -> good advice on attempt 3)")

# 2. every attempt fails -> no advice, but counts intact and focus still non-empty
digest = run_with(lambda n: BAD_ROWS, expect_attempts=3)
assert all(not t.get("advice") for t in digest["top_tags"])
assert digest["focus"], "focus must stay non-empty after total failure"
assert digest["corrections"] > 0, "SQL correction count must be untouched"
assert len(digest["top_tags"]) >= 2, "SQL tag rows must be untouched"
print("test_digest_retry: OK (total failure -> graceful focus, SQL counts intact)")

print("test_digest_retry: ALL PASS")
