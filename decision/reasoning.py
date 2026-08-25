"""The LLM stage: prose and classification, and nothing else.

**What the model is asked for.** Four things per situation, all of them language or a
choice from a supplied list:

1. ``root_cause`` — a failure category **code chosen from the candidate list supplied in
   the prompt**. §E18 rule 3 constrains the classification to the modes declared for the
   machine's type, and §16.5 requires root cause to be "stated as a hypothesis rather
   than a certainty". A code outside the list is rejected by the caller.
2. ``root_cause_confidence`` — a proposal, which the caller caps against the corroboration
   count (§E18 rule 4).
3. ``recommended_action`` and ``recovery_plan`` — contract element 5 in prose.
4. ``reasoning_narrative`` — "what a manager reads to decide whether to trust it".

**What the model is never asked for.** Any number. §16.8: "Using an LLM for arithmetic
risk estimation or a threshold rule for business impact analysis are both architectural
errors." The probability, priority, units, margin, cost, downtime and deadline are all
computed before the prompt is built and are passed **into** it as settled facts, so the
model's job is to explain figures it cannot alter. AQ8 depends on exactly this split.

**One call per cycle, not one per recommendation.** Every escalated context in the cycle
goes into a single request and the response carries one object per context code. §46.5 puts
the call outside the transaction, so batching costs nothing in lock duration and
``prompt_token_count`` — which §E18 calls "the metric that proves the escalation gate is
paying for itself" — stays measurable per cycle.

**The key is read from the environment only.** ``GROQ_API_KEY`` comes from the process
environment or the project ``.env``; it appears in no source file. Its absence raises
:class:`LlmConfigurationError` rather than falling back to a template, because
``llm_model_name`` and ``llm_model_version`` are a provenance record and writing them for
output no model produced would corrupt the quality-attribution trail.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from decision.errors import LlmConfigurationError, LlmReasoningError

# Fixed by the project's standard. Recorded on every row for quality attribution.
LLM_MODEL_NAME = "groq"
LLM_MODEL_VERSION = "llama-3.3-70b-versatile"

ENV_KEY = "GROQ_API_KEY"

# Deterministic decoding. Combined with an unchanged prompt this is as reproducible as a
# hosted model allows; §E18 is explicit that LLM output is not regenerable, which is why
# the row is stored rather than recomputed.
TEMPERATURE = 0.0
SEED = 20260731
MAX_TOKENS = 4096

SYSTEM_PROMPT = (
    "You are the Decision Agent of FactoryFlow AI, an advisory manufacturing "
    "decision-support platform. You write for a factory manager who will read your "
    "output and decide what to do.\n\n"
    "Hard rules:\n"
    "1. You ADVISE. You never command a machine, never give a setpoint, and never "
    "state that an action has been taken.\n"
    "2. Every number in the situation you are given is already settled. Quote the "
    "numbers exactly as given. Never recompute, round, adjust or invent a number, "
    "and never state a failure probability other than the one supplied.\n"
    "3. Choose root_cause ONLY from the candidate codes supplied for that situation. "
    "Never invent a failure category.\n"
    "4. Frame root cause as a hypothesis, not a certainty.\n"
    "5. Be specific and concrete. Name the part, the measurement, the engineer, the "
    "team and the timing you were given. Generic advice is a failure.\n\n"
    "recommended_action is read beside a header that already shows the machine, the "
    "severity, the failure category, the probability, the deadline, the estimated "
    "downtime and the reference codes. Do not restate any of those. Express timing in "
    "operational terms a supervisor can act on, such as the next crew change, before "
    "the current batch ends, or within this shift; never repeat the deadline itself "
    "and never print an absolute date or time. Never mention an internal field name "
    "such as act_by. Write the maintenance guidance the header cannot convey: 80 to "
    "150 words of flowing prose in several short sentences rather than one long one, "
    "no headings and no bullet points, answering all six of these naturally:\n"
    "  - what the evidence indicates about the component's condition;\n"
    "  - why intervening now is worthwhile rather than later;\n"
    "  - the specific inspection or repair steps, including what to measure;\n"
    "  - which engineer and maintenance team should carry it out;\n"
    "  - when to do it, tied to a real production opportunity;\n"
    "  - what to monitor afterwards to confirm the condition is resolved.\n"
    "Open with the condition or the action. Never open with a preamble, and never "
    "with 'It is advised', 'It is recommended', 'Based on the available information' "
    "or a machine name. State each fact once. The first sentence may be read alone on "
    "a phone, so lead with the most decision-critical content.\n\n"
    "Reply with a single JSON object and nothing else. Its keys are the situation "
    "reference codes you were given. Each value is an object with exactly these keys: "
    "root_cause (a candidate code), root_cause_confidence (high, moderate or low), "
    "recommended_action (as specified above), recovery_plan "
    "(2-5 sentences: part availability, expected downtime, schedule impact, and a "
    "specific contingency trigger), reasoning_narrative (3-5 short paragraphs: what "
    "the evidence shows, what it means, the business consequence, and why the "
    "recommended timing is right)."
)


@dataclass(frozen=True)
class ReasoningRequest:
    """One situation the model is asked to reason about.

    ``facts`` holds only settled values. Nothing in it is for the model to change.
    """

    reference: str
    facts: dict[str, Any]
    candidates: list[dict[str, str]]


@dataclass
class ReasoningResult:
    """The model's four outputs for one situation, plus the call's cost."""

    root_cause_code: str
    root_cause_confidence: str
    recommended_action: str
    recovery_plan: str
    reasoning_narrative: str


@dataclass
class ReasoningBatch:
    """Everything one model call produced."""

    results: dict[str, ReasoningResult]
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: int


def load_api_key(env_path: str | Path | None = None) -> str:
    """The Groq key, from the environment or the project ``.env``.

    Never from source. Raises when absent rather than continuing, per the configuration
    contract: a missing key is a deployment fault, and a recommendation generated without
    a model would be attributed to one.
    """
    key = os.environ.get(ENV_KEY, "").strip()
    if key:
        return key

    candidate = Path(env_path) if env_path is not None else Path.cwd() / ".env"
    if candidate.is_file():
        try:
            from dotenv import dotenv_values

            values = dotenv_values(candidate)
        except ImportError:  # pragma: no cover - dotenv ships with the project
            values = _parse_env(candidate)
        key = str(values.get(ENV_KEY) or "").strip()
        if key:
            return key

    raise LlmConfigurationError(
        "%s is not set. The Decision Agent reads it from the process environment or "
        "from %s, and never from source. Add a line reading '%s=<your key>' to that "
        "file, or export it, then run again. The agent does not continue without a "
        "model: a recommendation records which model reasoned, and there would be "
        "nothing truthful to record." % (ENV_KEY, candidate, ENV_KEY)
    )


def _parse_env(path: Path) -> dict[str, str]:
    """Minimal ``.env`` reader, used only if python-dotenv is unavailable."""
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        found[name.strip()] = value.strip().strip('"').strip("'")
    return found


class GroqReasoner:
    """Calls ``llama-3.3-70b-versatile`` once per decision cycle.

    The model name and version are fixed constants: there is no selection logic, no
    fallback model and no second provider, because ``llm_model_version`` exists so that a
    change in recommendation quality is attributable to a change in model, and silent
    substitution would destroy that.
    """

    model_name = LLM_MODEL_NAME
    model_version = LLM_MODEL_VERSION

    def __init__(
        self,
        *,
        api_key: str | None = None,
        env_path: str | Path | None = None,
    ) -> None:
        self.api_key = api_key or load_api_key(env_path)
        self._client: Any | None = None

    def _get_client(self) -> Any:
        """The Groq client, constructed once and reused for the process lifetime."""
        if self._client is None:
            try:
                from groq import Groq
            except ImportError as exc:
                raise LlmConfigurationError(
                    "the 'groq' package is not installed, so the Decision Agent "
                    "cannot reach %s. Install it and run again." % LLM_MODEL_VERSION
                ) from exc
            self._client = Groq(api_key=self.api_key)
        return self._client

    def reason(self, requests: list[ReasoningRequest]) -> ReasoningBatch:
        """One request covering every situation in the cycle."""
        import time

        if not requests:
            return ReasoningBatch(results={}, prompt_tokens=0, completion_tokens=0,
                                  duration_ms=0)

        payload = {
            "situations": [
                {
                    "reference": request.reference,
                    "settled_facts": request.facts,
                    "root_cause_candidates": request.candidates,
                }
                for request in requests
            ]
        }
        client = self._get_client()
        started = time.perf_counter()
        try:
            completion = client.chat.completions.create(
                model=LLM_MODEL_VERSION,
                temperature=TEMPERATURE,
                seed=SEED,
                max_tokens=MAX_TOKENS,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, default=str)},
                ],
            )
        except Exception as exc:  # noqa: BLE001 -- re-raised as a typed error
            raise LlmReasoningError(
                "the %s call failed: %s: %s"
                % (LLM_MODEL_VERSION, type(exc).__name__, exc)
            ) from exc
        duration_ms = int((time.perf_counter() - started) * 1000)

        body = completion.choices[0].message.content or ""
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LlmReasoningError(
                "%s returned a body that is not JSON: %s"
                % (LLM_MODEL_VERSION, body[:200])
            ) from exc
        if not isinstance(parsed, dict):
            raise LlmReasoningError(
                "%s returned %s at the top level; an object keyed by situation "
                "reference was required" % (LLM_MODEL_VERSION, type(parsed).__name__)
            )

        usage = getattr(completion, "usage", None)
        return ReasoningBatch(
            results=parse_results(parsed, [r.reference for r in requests]),
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            duration_ms=duration_ms,
        )


def parse_results(
    parsed: dict[str, Any],
    references: list[str],
) -> dict[str, ReasoningResult]:
    """Read one result per requested reference, refusing a partial response.

    A missing entry is an error rather than a gap to fill: the alternative is a
    recommendation whose prose came from a template while ``llm_model_name`` claims a
    model wrote it.
    """
    # Some responses nest the situations under a single key; accept that shape too.
    if not any(reference in parsed for reference in references):
        for value in parsed.values():
            if isinstance(value, dict) and any(r in value for r in references):
                parsed = value
                break

    results: dict[str, ReasoningResult] = {}
    missing: list[str] = []
    for reference in references:
        entry = parsed.get(reference)
        if not isinstance(entry, dict):
            missing.append(reference)
            continue
        results[reference] = ReasoningResult(
            root_cause_code=str(entry.get("root_cause", "")).strip().upper(),
            root_cause_confidence=str(
                entry.get("root_cause_confidence", "")).strip().lower(),
            recommended_action=str(entry.get("recommended_action", "")).strip(),
            recovery_plan=str(entry.get("recovery_plan", "")).strip(),
            reasoning_narrative=str(entry.get("reasoning_narrative", "")).strip(),
        )
    if missing:
        raise LlmReasoningError(
            "%s returned no entry for %d of %d situations (%s). A partial response is "
            "refused rather than filled in."
            % (LLM_MODEL_VERSION, len(missing), len(references), ", ".join(missing))
        )
    return results
