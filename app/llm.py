"""
LLM call wrapper for QA test-case generation.

Provider: Groq (llama-3.1-70b-versatile or similar) - free tier, fast,
OpenAI-compatible-ish client. Swappable: everything provider-specific is
isolated to `_call_groq`; the retry/validation policy below it doesn't
care which provider produced the raw text.

STRUCTURED OUTPUT / FAILURE POLICY (this is the part the assignment
specifically flags as easy to hand-wave):

1. Prompt asks for JSON ONLY, matching a schema we show it in the prompt.
2. Response is parsed and validated against the `TestCase` pydantic model
   (a list of 3-5 of them). Validation failure = malformed output.
3. On malformed output: retry ONCE with a follow-up message that includes
   the parse/validation error and the original bad output, asking the
   model to fix it. LLMs are often one correction away from valid JSON
   when told exactly what was wrong with the last attempt - a blind
   identical retry is less likely to help than a corrective one.
4. If the retry also fails validation: do NOT store a fabricated/partial
   result and do NOT silently drop the request. Store a Generation record
   with status="llm_failed", the raw failed output (for debugging), and
   an empty test_cases list. This is a deliberate choice - see APPROACH.md
   decision log Q1 - a generation that silently contains 2 valid test
   cases and 1 hallucinated-schema garbage entry is worse than one that
   visibly failed, because "silently wrong" is exactly the failure mode
   the assignment warns about. Callers/retrieval API surface status
   explicitly rather than ever presenting partial/garbage results as if
   they were complete.
5. We do NOT auto-retry indefinitely or auto-regenerate on stale reads
   (that's explicitly out of scope) - one retry, then fail loud.
"""
import json
import os
from dataclasses import dataclass

from pydantic import ValidationError

from app.schemas import TestCase

PROMPT_TEMPLATE = """You are a QA engineer generating test case ideas for a home medical device \
(a blood pressure monitor). You will be given one or more sections from the device's \
technical/regulatory manual. Generate 3 to 5 concrete, executable test cases based ONLY on \
the text provided - do not invent behavior, thresholds, or error codes that are not stated \
or reasonably implied by the text.

Return ONLY a JSON array (no markdown fences, no prose before or after) where each element has \
exactly these fields:
- "title": short string
- "preconditions": string describing device/system state before the test
- "steps": array of strings, each one concrete action
- "expected_result": string, concrete and verifiable
- "priority": one of "high", "medium", "low"

SOURCE SECTIONS:
---
{source_text}
---

JSON array only:"""


@dataclass
class LLMResult:
    status: str  # "ok" | "llm_failed"
    test_cases: list[TestCase]
    raw_output: str | None = None
    error: str | None = None


def _call_groq(prompt: str) -> str:
    from groq import Groq
    client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
    resp = client.chat.completions.create(
        model="llama-3.1-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=1500,
    )
    return resp.choices[0].message.content


def _parse_and_validate(raw: str) -> list[TestCase]:
    cleaned = raw.strip()
    # tolerate models that wrap in ```json fences despite instructions
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.split("\n", 1)[-1] if cleaned.lower().startswith("json") else cleaned
    data = json.loads(cleaned)  # raises json.JSONDecodeError on malformed JSON
    if not isinstance(data, list) or not (1 <= len(data) <= 8):
        raise ValueError(f"expected a JSON array of 1-8 items, got: {type(data)}")
    return [TestCase(**item) for item in data]  # raises ValidationError per item


def generate_test_cases(source_text: str) -> LLMResult:
    prompt = PROMPT_TEMPLATE.format(source_text=source_text)

    raw = _call_groq(prompt)
    try:
        cases = _parse_and_validate(raw)
        return LLMResult(status="ok", test_cases=cases, raw_output=raw)
    except (json.JSONDecodeError, ValidationError, ValueError) as e:
        first_error = str(e)

    # one corrective retry, telling the model exactly what was wrong
    retry_prompt = (
        prompt
        + f"\n\nYour previous response failed validation with this error:\n{first_error}\n"
        + f"Your previous response was:\n{raw}\n\n"
        + "Return ONLY a corrected JSON array matching the schema exactly."
    )
    raw2 = _call_groq(retry_prompt)
    try:
        cases = _parse_and_validate(raw2)
        return LLMResult(status="ok", test_cases=cases, raw_output=raw2)
    except (json.JSONDecodeError, ValidationError, ValueError) as e:
        return LLMResult(
            status="llm_failed",
            test_cases=[],
            raw_output=raw2,
            error=f"failed after retry: {e}",
        )
