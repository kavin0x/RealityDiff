from __future__ import annotations

from typing import Any

from realitydiff.models import ClaimState, Evidence, Prediction, Unknown, utcnow
from realitydiff.xai import LanguageModel, parse_json_object


INIT_PROMPT = """You are Reality Diff, a belief-versioning engine. You do not chat.
You turn a claim into a living, evidence-weighted belief object.

CLAIM:
{claim}

Search the live web and X for current evidence. Prefer primary sources:
company docs, SEC filings, official blogs, reputable reporting, patents, job posts.

Return ONLY a JSON object with this exact shape:
{{
  "statement": "canonical claim, precise and testable",
  "refined_statement": "more precise operational version of the claim",
  "confidence": 0-100 number (not a string),
  "reason": "why this confidence, citing the strongest evidence",
  "summary": "one paragraph current belief",
  "evidence_for": [
    {{
      "statement": "what the evidence actually shows",
      "source_url": "https://...",
      "source_title": "title",
      "weight": 0.0-1.0,
      "notes": "how this supports the claim and caveats"
    }}
  ],
  "evidence_against": [ same shape ],
  "unknowns": [
    {{"question": "...", "why_it_matters": "..."}}
  ],
  "predictions": [
    {{
      "statement": "a falsifiable near-term prediction implied by the claim",
      "due": "YYYY-MM or null",
      "status": "open",
      "how_to_falsify": "what would prove this wrong"
    }}
  ]
}}

Calibration:
- 50 = truly mixed / unknown.
- Do not exceed 85 without an official confirmation of the exact claim.
- Rumors and leaks cap around 65 unless independently corroborated.
- Official denials or contradictory shipping products should drop confidence.
- Separate "LLM features inside Siri" from "replace Siri's architecture".
"""


WATCH_PROMPT = """You are Reality Diff. Update a living claim from NEW internet evidence.
Do not rewrite history. Preserve existing evidence ids when the same source/fact remains.

CLAIM:
{claim}

CURRENT STATE JSON:
{state}

Previously known source URLs:
{urls}

Search the live web and X for anything new: docs, code, job posts, earnings, leaks,
official statements, shipping products, regulatory filings.

Return ONLY JSON:
{{
  "changed": true or false,
  "reason": "if changed, one paragraph: what new evidence appeared and how it moves confidence",
  "statement": "canonical claim",
  "refined_statement": "...",
  "confidence": 0-100,
  "summary": "...",
  "evidence_for": [
    {{
      "id": "keep existing id if same fact, else omit",
      "statement": "...",
      "source_url": "https://...",
      "source_title": "...",
      "weight": 0.0-1.0,
      "notes": "..."
    }}
  ],
  "evidence_against": [ same ],
  "unknowns": [
    {{"id": "keep if same question", "question": "...", "why_it_matters": "..."}}
  ],
  "predictions": [
    {{
      "id": "keep if same prediction",
      "statement": "...",
      "due": "YYYY-MM or null",
      "status": "open|confirmed|falsified",
      "how_to_falsify": "..."
    }}
  ],
  "new_source_urls": ["..."]
}}

Rules:
- changed=false if you found only duplicates of existing evidence.
- Move confidence conservatively. A single rumor is +3 to +8, not +30.
- Direct official documentation that confirms the refined claim can jump more.
- Direct official contradiction should drop more.
- Keep prior evidence unless retracted or obsolete; mark obsolescence in notes.
"""


class Reasoner:
    def __init__(self, model: LanguageModel) -> None:
        self.model = model

    def initialize(self, claim: str) -> tuple[ClaimState, str]:
        result = self.model.complete(INIT_PROMPT.format(claim=claim.strip()), search=True)
        data = parse_json_object(result["text"])
        state = self._state_from_payload(data, fallback_statement=claim)
        reason = str(data.get("reason") or "Initial internet survey.")
        return state, reason

    def update(self, claim: str, current: ClaimState) -> tuple[ClaimState, str, bool]:
        urls = sorted(
            {
                item.source_url
                for item in current.evidence_for + current.evidence_against
                if item.source_url
            }
        )
        result = self.model.complete(
            WATCH_PROMPT.format(
                claim=claim.strip(),
                state=current.model_dump_json(indent=2),
                urls="\n".join(urls) or "(none)",
            ),
            search=True,
        )
        data = parse_json_object(result["text"])
        changed = bool(data.get("changed"))
        reason = str(data.get("reason") or "")
        if not changed:
            return current, reason or "No new evidence.", False
        state = self._state_from_payload(data, fallback_statement=current.statement, previous=current)
        if state.tree_hash() == current.tree_hash():
            return current, reason or "No material change.", False
        return state, reason or "New evidence updated the belief.", True

    def _state_from_payload(
        self,
        data: dict[str, Any],
        *,
        fallback_statement: str,
        previous: ClaimState | None = None,
    ) -> ClaimState:
        prev_for = {item.id: item for item in previous.evidence_for} if previous else {}
        prev_against = {item.id: item for item in previous.evidence_against} if previous else {}
        prev_unknowns = {item.id: item for item in previous.unknowns} if previous else {}
        prev_preds = {item.id: item for item in previous.predictions} if previous else {}

        return ClaimState(
            statement=str(data.get("statement") or fallback_statement).strip(),
            refined_statement=str(data.get("refined_statement") or "").strip(),
            confidence=_coerce_number(data.get("confidence"), default=50.0),
            summary=str(data.get("summary") or "").strip(),
            evidence_for=self._parse_evidence(data.get("evidence_for") or [], prev_for),
            evidence_against=self._parse_evidence(data.get("evidence_against") or [], prev_against),
            unknowns=self._parse_unknowns(data.get("unknowns") or [], prev_unknowns),
            predictions=self._parse_predictions(data.get("predictions") or [], prev_preds),
        )

    def _parse_evidence(self, items: list[Any], previous: dict[str, Evidence]) -> list[Evidence]:
        parsed: list[Evidence] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            statement = str(item.get("statement") or "").strip()
            if not statement:
                continue
            source_url = str(item.get("source_url") or "").strip() or None
            if source_url and not source_url.startswith(("http://", "https://")):
                source_url = None
            candidate = Evidence.make(
                statement,
                source_url=source_url,
                source_title=item.get("source_title") or None,
                weight=_coerce_number(item.get("weight"), default=0.5, lo=0.0, hi=1.0),
                notes=str(item.get("notes") or ""),
                retrieved_at=utcnow(),
            )
            requested_id = str(item.get("id") or "")
            if requested_id and requested_id in previous:
                prior = previous[requested_id]
                candidate = candidate.model_copy(
                    update={
                        "id": requested_id,
                        "retrieved_at": prior.retrieved_at,
                    }
                )
            if candidate.id in seen:
                continue
            seen.add(candidate.id)
            parsed.append(candidate)
        parsed.sort(key=lambda item: item.weight, reverse=True)
        return parsed[:12]

    def _parse_unknowns(self, items: list[Any], previous: dict[str, Unknown]) -> list[Unknown]:
        parsed: list[Unknown] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            if not question:
                continue
            unknown = Unknown.make(question, str(item.get("why_it_matters") or ""))
            requested_id = str(item.get("id") or "")
            if requested_id and requested_id in previous:
                unknown = unknown.model_copy(update={"id": requested_id})
            if unknown.id in seen:
                continue
            seen.add(unknown.id)
            parsed.append(unknown)
        return parsed[:8]

    def _parse_predictions(self, items: list[Any], previous: dict[str, Prediction]) -> list[Prediction]:
        parsed: list[Prediction] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            statement = str(item.get("statement") or "").strip()
            if not statement:
                continue
            status = item.get("status") or "open"
            if status not in {"open", "confirmed", "falsified"}:
                status = "open"
            prediction = Prediction.make(
                statement,
                due=item.get("due") or None,
                status=status,
                how_to_falsify=str(item.get("how_to_falsify") or ""),
            )
            requested_id = str(item.get("id") or "")
            if requested_id and requested_id in previous:
                prediction = prediction.model_copy(update={"id": requested_id})
            if prediction.id in seen:
                continue
            seen.add(prediction.id)
            parsed.append(prediction)
        return parsed[:8]


def _coerce_number(value: Any, *, default: float, lo: float = 0.0, hi: float = 100.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace("%", "")
        try:
            number = float(text)
        except ValueError:
            return default
    return max(lo, min(hi, number))
