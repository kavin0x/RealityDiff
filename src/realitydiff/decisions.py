"""OpenRouter Decisions API (System One shape).

Decision models return probabilities, not prose. Callers threshold those
numbers. Output tokens are not billed. The endpoint is still under /api/alpha.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from realitydiff.providers import ProviderError, redact_secrets, safe_error_message

OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_DECISION_MODEL = "liquid/d1.1"
FALLBACK_DECISION_MODEL = "openai/gpt-6-luna-decisions"
DECISION_TIMEOUT = 12.0

MATERIAL_MIN = 0.80
MARGIN_MIN = 0.15
RUMOR_DELTA = 4.0
CONTRADICTION_DELTA = -6.0

_QUESTIONS: dict[str, Any] = {
    "material": {
        "type": "noul",
        "instructions": (
            "Is the changed page new evidence about this claim, rather than a restyle, "
            "a timestamp, or a duplicate of evidence the belief already cites?"
        ),
        "criteria": {
            "true": "The page change is new evidence about the claim.",
            "false": "The change is a restyle, a timestamp, or a duplicate of evidence already in the belief.",
        },
    },
    "kind": {
        "type": "choice",
        "instructions": "What kind of change is this, if any?",
        "criteria": {
            "none": "No meaningful change, or only a duplicate or restyle.",
            "rumor": "An unverified report, leak, or secondary account that the claim may be moving.",
            "contradiction": "A source that cuts against the claim without officially settling it.",
            "official": "A primary source confirms or denies the claim itself.",
            "resolved": "The real-world outcome of the claim is now known.",
        },
    },
    "size": {
        "type": "score",
        "instructions": "How large an update does this page change deserve?",
        "criteria": ["unchanged", "nudge", "rewrite"],
    },
}


def default_decision_model() -> str:
    return (os.environ.get("OPENROUTER_DECISION_MODEL") or DEFAULT_DECISION_MODEL).strip()


def confidence_delta(kind: str) -> float:
    if kind == "rumor":
        return RUMOR_DELTA
    if kind == "contradiction":
        return CONTRADICTION_DELTA
    return 0.0


def decision_reason(decision: Decision) -> str:
    return f"{decision.kind} · {decision.material:.2f}"


@dataclass(frozen=True)
class Decision:
    material: float
    kind: str
    margin: float
    size: str
    model: str = ""


def gate(decision: Decision) -> str:
    """skip, hold, nudge, or escalate. Ambiguous splits are held for the slow model."""
    if decision.margin < MARGIN_MIN:
        return "hold"
    if decision.kind in {"official", "resolved"} or (
        decision.size == "rewrite" and decision.kind != "none"
    ):
        return "escalate"
    if (
        decision.material >= MATERIAL_MIN
        and decision.size == "nudge"
        and decision.kind in {"rumor", "contradiction"}
    ):
        return "nudge"
    return "skip"


def questions() -> dict[str, Any]:
    return _QUESTIONS


class DecisionsClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str | None = None,
        timeout: float = DECISION_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError("OPENROUTER_API_KEY is not set")
        self._api_key = api_key
        self.model = (model or default_decision_model()).strip() or DEFAULT_DECISION_MODEL
        self.timeout = timeout
        self._transport = transport

    def decide(self, state: dict[str, Any]) -> Decision:
        try:
            return self._once(self.model, state)
        except ProviderError as exc:
            if self.model == FALLBACK_DECISION_MODEL or not _fallbackable(exc):
                raise
            return self._once(FALLBACK_DECISION_MODEL, state)

    def _once(self, model: str, state: dict[str, Any]) -> Decision:
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                response = client.post(
                    OPENROUTER_DECISIONS_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/kavin0x/RealityDiff",
                        "X-OpenRouter-Title": "Reality Diff",
                    },
                    json={"model": model, "state": state, "questions": _QUESTIONS},
                )
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError("Timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(safe_error_message(exc, self._api_key)) from exc
        if response.status_code >= 400:
            snippet = redact_secrets((response.text or "")[:200], self._api_key)
            raise ProviderError(f"HTTP {response.status_code}: {snippet}".strip())
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("Provider returned non-JSON") from exc
        if not isinstance(body, dict):
            raise ProviderError("Provider returned an unexpected body")
        err = body.get("error")
        if err:
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise ProviderError(redact_secrets(str(message or "Provider error"), self._api_key)[:180])
        used = str(body.get("model") or model)
        return decision_from_body(body, model=used)


def decision_from_body(body: dict[str, Any], *, model: str) -> Decision:
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise ProviderError("Decision returned no answers")
    material_answer = answers.get("material")
    kind_answer = answers.get("kind")
    if not isinstance(material_answer, dict) or not isinstance(kind_answer, dict):
        raise ProviderError("Decision returned an unexpected body")
    material = _unit_float(material_answer.get("noul", material_answer.get("probability")))
    kind, margin = _kind_margin(kind_answer)
    size_answer = answers.get("size")
    size = _size_label(size_answer if isinstance(size_answer, dict) else {})
    return Decision(material=material, kind=kind, margin=margin, size=size, model=model)


def _fallbackable(exc: ProviderError) -> bool:
    text = str(exc)
    return "HTTP 404" in text or "HTTP 529" in text


def _unit_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderError("Decision returned an unexpected body") from exc
    return max(0.0, min(1.0, number))


def _kind_margin(answer: dict[str, Any]) -> tuple[str, float]:
    probs = answer.get("probabilities")
    if isinstance(probs, dict) and probs:
        ranked: list[tuple[float, str]] = []
        for key, value in probs.items():
            try:
                ranked.append((float(value), str(key).strip().lower()))
            except (TypeError, ValueError):
                continue
        ranked.sort(reverse=True)
        if ranked:
            top_p, top_k = ranked[0]
            second = ranked[1][0] if len(ranked) > 1 else 0.0
            return top_k, max(0.0, top_p - second)
    kind = str(answer.get("choice") or "none").strip().lower() or "none"
    return kind, 0.0


def _size_label(answer: dict[str, Any]) -> str:
    try:
        number = float(answer.get("score"))
    except (TypeError, ValueError):
        return "unchanged"
    if number >= 1.5:
        return "rewrite"
    if number >= 0.5:
        return "nudge"
    return "unchanged"
