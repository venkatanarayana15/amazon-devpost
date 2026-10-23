"""Optional LLM narration layer.

The LLM is a copy editor, not a decision maker. It receives the already-final
deterministic answer plus structured facts and may only rewrite the wording.

Three guardrails keep it from becoming a liability:
  1. Any failure (no key, timeout, bad JSON) silently falls back to the
     deterministic text, so the demo can never break because of the model.
  2. Output longer than 900 characters is rejected.
  3. Any number the model invents that was not in the source text is rejected,
     which prevents the most common hallucination class in ops tooling.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from . import config

SYSTEM = (
    "You are the narration layer of DocOps Commander, an operations agent. "
    "You receive a factual answer that has ALREADY been computed and approved by a policy engine. "
    "Rewrite it so it sounds like a competent on-call operator speaking to a colleague. "
    "Hard rules: keep every number, job id, vendor name and policy id exactly as given; "
    "never invent a fact, a number, or an action; never claim something was executed; "
    "keep it under 90 words; output prose only, no markdown, no bullet points, no preamble."
)


def _numbers(text: str) -> set[str]:
    return {token.replace(",", "") for token in re.findall(r"\d[\d,]*", text)}


def polish(text: str, facts: dict) -> tuple[str, str]:
    """Return (narration, narrator_label)."""
    if not config.LLM_ENABLED:
        return text, "deterministic"

    body = {
        "model": config.LLM_MODEL,
        "temperature": 0.2,
        "max_tokens": 220,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": "FACTS (JSON):\n"
                + json.dumps(facts, default=str)
                + "\n\nAPPROVED ANSWER:\n"
                + text,
            },
        ],
    }

    request = urllib.request.Request(
        f"{config.LLM_BASE_URL}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.LLM_API_KEY}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=config.LLM_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
        candidate = payload["choices"][0]["message"]["content"].strip()
    except (urllib.error.URLError, KeyError, IndexError, ValueError, TimeoutError, OSError):
        return text, "deterministic-fallback"

    if not candidate or len(candidate) > 900:
        return text, "deterministic-fallback"

    allowed = _numbers(text) | _numbers(json.dumps(facts, default=str))
    if _numbers(candidate) - allowed:
        return text, "deterministic-guarded"

    return candidate, f"llm:{config.LLM_MODEL}"
