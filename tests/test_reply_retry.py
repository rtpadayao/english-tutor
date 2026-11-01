"""tutor.reply transient-failure hardening: fake-LLM unit test (no Ollama needed).

2026-11-01 smoke run: one transient Ollama blip mid-suite surfaced as a 500 and
killed the run. reply() must absorb a single attempt failure with one retry; a
second genuine failure still raises.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import tutor  # noqa: E402

VALID = json.dumps({
    "reply": "Sure — here is how I would say it.",
    "corrections": [],
    "rephrase": {"professional": "I'm running a bit behind schedule.",
                 "variants": ["I'm running late."],
                 "usage": "Use when informing a client you will join late."},
})


def run_with(fake, expect_calls: int, expect_raise: bool):
    calls = {"n": 0}

    def wrapper(messages, schema, num_predict=420, temperature=0.6, timeout=180):
        calls["n"] += 1
        return fake(calls["n"], temperature)

    tutor.ollama_chat = wrapper
    try:
        result, elapsed = tutor.reply("how do I say I am running late", mode="rephrase")
        assert not expect_raise, f"expected a raise, got {result}"
    except RuntimeError:
        assert expect_raise, "reply raised but a recovery was expected"
    assert calls["n"] == expect_calls, f"expected {expect_calls} calls, got {calls['n']}"


# 1. first attempt empty, retry succeeds -> no raise, exactly 2 calls
def empty_then_good(n, temperature):
    if n == 1:
        assert temperature == 0.6
        return ""  # parse() turns empty content into RuntimeError
    assert temperature == 0.3, "retry must drop the temperature"
    return VALID


run_with(empty_then_good, expect_calls=2, expect_raise=False)
print("test_reply_retry: OK (empty reply -> retry at temp 0.3 -> success, 2 calls)")

# 2. both attempts fail -> raises after exactly 2 calls (the 500 is then real)
run_with(lambda n, t: "", expect_calls=2, expect_raise=True)
print("test_reply_retry: OK (persistent failure -> raises after 2 calls)")

# 3. healthy path stays single-call (no latency cost when nothing fails)
run_with(lambda n, t: VALID, expect_calls=1, expect_raise=False)
print("test_reply_retry: OK (healthy path -> single call)")

print("test_reply_retry: ALL PASS")
