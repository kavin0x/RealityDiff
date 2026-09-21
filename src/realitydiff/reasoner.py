from __future__ import annotations

import json
from typing import Any

from realitydiff.compress import compact_belief, is_material_change
from realitydiff.models import Citation, ClaimOutcome, ClaimState, Evidence, Prediction, Unknown, utcnow
from realitydiff.xai import LanguageModel, parse_json_object, XAIError


SYSTEM_PROMPT = """You are Reality Diff, a belief-versioning engine. You do not chat.
You turn claims into living, evidence-weighted belief objects and update them from the live internet.

Search the live web and X. Prefer primary sources: company docs, SEC filings, official blogs,
reputable reporting, patents, job posts.

You have a close_claim tool. Call it only when the claim's real-world outcome is now known
from primary evidence (official confirmation, shipping product, recorded result, court ruling).
Do not close on rumors, high confidence, or expired due dates without an actual outcome.
After close_claim, still return the JSON belief object with changed=true, final confidence,
and evidence for the outcome.

Citations are mandatory. Every evidence item MUST include a https source_url from search.
If you cannot cite a fact, put it in unknowns instead of evidence.
Return ONLY a JSON object. No markdown outside JSON."""

INIT_USER = """CLAIM:
{claim}

Return JSON with this exact shape:
{{
  "statement": "canonical claim, precise and testable",
  "refined_statement": "more precise operational version of the claim",
  "confidence": 0-100 number (not a string),
  "reason": "why this confidence, citing the strongest evidence with URLs",
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


WATCH_USER = """Update this living claim from NEW internet evidence.
Do not rewrite history. Preserve existing evidence ids when the same source/fact remains.
Do not invent a change. If nothing material moved, set changed=false.

CLAIM:
{claim}

CURRENT STATE (compressed):
{state}

Previously known source URLs:
{urls}

Search the live web and X for anything new: docs, code, job posts, earnings, leaks,
official statements, shipping products, regulatory filings.

Return ONLY JSON:
{{
  "changed": true or false,
  "reason": "if changed, one paragraph: what new evidence appeared and how it moves confidence, with URLs",
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
- changed=false if you found only duplicates of existing evidence, or only reworded the same facts.
- Ignore retrieved timestamps and stylistic rewrites. A commit needs new URLs, status changes, or ≥1 point of confidence.
- Move confidence conservatively. A single rumor is +3 to +8, not +30.
- Direct official documentation that confirms the refined claim can jump more.
- Direct official contradiction should drop more.
- Keep prior evidence unless retracted or obsolete; mark obsolescence in notes.
- If the claim itself has resolved, call close_claim and set changed=true.
"""


class Reasoner:
    def __init__(self, model: LanguageModel) -> None:
        self.model = model

    def initialize(self, claim: str, *, conv_id: str | None = None) -> tuple[ClaimState, str, dict[str, Any]]:
        result = self.model.complete(
            INIT_USER.format(claim=claim.strip()),
            search=True,
            system=SYSTEM_PROMPT,
            conv_id=conv_id or "realitydiff-init",
            cache_key="realitydiff-init-v3",
        )
        data = _payload_from_result(
            result,
            required=not bool(isinstance(result.get("close_claim"), dict) and result.get("close_claim", {}).get("ok")),
        )
        if not data:
            data = {"statement": claim.strip(), "confidence": 50, "reason": ""}
        state = self._state_from_payload(data, fallback_statement=claim, citations=result.get("citations") or [])
        state = apply_close_claim(state, result.get("close_claim"))
        reason = str(data.get("reason") or (state.outcome.summary if state.outcome else "Initial internet survey."))
        return state, reason, result

    def update(
        self,
        claim: str,
        current: ClaimState,
        *,
        conv_id: str | None = None,
        previous_response_id: str | None = None,
        compaction: dict[str, Any] | None = None,
    ) -> tuple[ClaimState, str, bool, dict[str, Any]]:
        urls = sorted(
            {
                item.source_url
                for item in current.evidence_for + current.evidence_against
                if item.source_url
            }
        )
        result = self.model.complete(
            WATCH_USER.format(
                claim=claim.strip(),
                state=json.dumps(compact_belief(current), ensure_ascii=False, separators=(",", ":")),
                urls="\n".join(urls) or "(none)",
            ),
            search=True,
            system=SYSTEM_PROMPT,
            conv_id=conv_id,
            cache_key=conv_id or "realitydiff-watch-v3",
            previous_response_id=previous_response_id,
            compaction=compaction,
        )
        close = result.get("close_claim") if isinstance(result.get("close_claim"), dict) else None
        data = _payload_from_result(result, required=not bool(close and close.get("ok")))
        changed = bool(data.get("changed")) or bool(close and close.get("ok"))
        reason = str(data.get("reason") or "")
        if not changed:
            return current, reason or "No new evidence.", False, result
        if data:
            state = self._state_from_payload(
                data,
                fallback_statement=current.statement,
                previous=current,
                citations=result.get("citations") or [],
            )
        else:
            state = current
        state = apply_close_claim(state, close)
        if state.tree_hash() == current.tree_hash() or not is_material_change(current, state):
            note = reason.strip()
            detail = "No material change."
            if note:
                detail = f"No material change. {note}"
            return current, detail, False, result
        if state.outcome and not reason:
            reason = state.outcome.summary
        return state, reason or "New evidence updated the belief.", True, result

    def _state_from_payload(
        self,
        data: dict[str, Any],
        *,
        fallback_statement: str,
        previous: ClaimState | None = None,
        citations: list[dict[str, str]] | None = None,
    ) -> ClaimState:
        prev_for = {item.id: item for item in previous.evidence_for} if previous else {}
        prev_against = {item.id: item for item in previous.evidence_against} if previous else {}
        prev_unknowns = {item.id: item for item in previous.unknowns} if previous else {}
        prev_preds = {item.id: item for item in previous.predictions} if previous else {}

        evidence_for = self._parse_evidence(data.get("evidence_for") or [], prev_for)
        evidence_against = self._parse_evidence(data.get("evidence_against") or [], prev_against)
        cited = _parse_citations(citations or [], data)
        evidence_for, evidence_against = _attach_citations(evidence_for, evidence_against, cited)

        return ClaimState(
            statement=str(data.get("statement") or fallback_statement).strip(),
            refined_statement=str(data.get("refined_statement") or "").strip(),
            confidence=_coerce_number(data.get("confidence"), default=50.0),
            summary=str(data.get("summary") or "").strip(),
            evidence_for=evidence_for,
            evidence_against=evidence_against,
            unknowns=self._parse_unknowns(data.get("unknowns") or [], prev_unknowns),
            predictions=self._parse_predictions(data.get("predictions") or [], prev_preds),
            citations=cited,
            outcome=previous.outcome if previous else None,
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


def _payload_from_result(result: dict[str, Any], *, required: bool = True) -> dict[str, Any]:
    text = str(result.get("text") or "")
    if not text.strip():
        if required:
            return parse_json_object(text)
        return {}
    try:
        return parse_json_object(text)
    except XAIError:
        if required:
            raise
        return {}


def apply_close_claim(state: ClaimState, close: Any) -> ClaimState:
    if not isinstance(close, dict) or not close.get("ok"):
        return state
    verdict = close.get("verdict")
    if verdict not in {"confirmed", "falsified", "settled"}:
        return state
    summary = str(close.get("outcome") or "").strip()
    if not summary:
        return state
    confidence = close.get("confidence")
    if confidence is None:
        if verdict == "confirmed":
            confidence = 100.0
        elif verdict == "falsified":
            confidence = 0.0
        else:
            confidence = state.confidence
    outcome = ClaimOutcome(
        verdict=verdict,
        summary=summary,
        source_url=close.get("source_url") or None,
        source_title=close.get("source_title") or None,
    )
    evidence_for = list(state.evidence_for)
    evidence_against = list(state.evidence_against)
    source_url = outcome.source_url
    if source_url:
        item = Evidence.make(
            summary,
            source_url=source_url,
            source_title=outcome.source_title,
            weight=1.0,
            notes="Primary source for the final outcome.",
        )
        bucket = evidence_for if verdict != "falsified" else evidence_against
        if all((existing.source_url or "").rstrip("/") != source_url.rstrip("/") for existing in bucket):
            bucket.insert(0, item)
    return state.model_copy(
        update={
            "confidence": _coerce_number(confidence, default=state.confidence),
            "outcome": outcome,
            "summary": summary if not state.summary else state.summary,
            "evidence_for": evidence_for[:12],
            "evidence_against": evidence_against[:12],
        }
    )


def _parse_citations(raw: list[dict[str, str]], data: dict[str, Any]) -> list[Citation]:
    found: list[Citation] = []
    seen: set[str] = set()

    def add(url: str, title: str = "") -> None:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            return
        seen.add(url)
        found.append(Citation.make(url, title))

    for item in raw:
        add(item.get("url") or "", item.get("title") or "")
    extra = data.get("new_source_urls") or []
    if isinstance(extra, list):
        for url in extra:
            if isinstance(url, str):
                add(url)
    return found[:24]


def _attach_citations(
    for_items: list[Evidence],
    against_items: list[Evidence],
    citations: list[Citation],
) -> tuple[list[Evidence], list[Evidence]]:
    used = {(item.source_url or "").rstrip("/") for item in for_items + against_items if item.source_url}
    unused = [c for c in citations if c.url.rstrip("/") not in used]
    patched_for: list[Evidence] = []
    leftover = list(unused)
    for item in for_items:
        if item.source_url or not leftover:
            patched_for.append(item)
            continue
        cite = leftover.pop(0)
        patched_for.append(
            item.model_copy(update={"source_url": cite.url, "source_title": item.source_title or cite.title})
        )
    patched_against: list[Evidence] = []
    for item in against_items:
        if item.source_url or not leftover:
            patched_against.append(item)
            continue
        cite = leftover.pop(0)
        patched_against.append(
            item.model_copy(update={"source_url": cite.url, "source_title": item.source_title or cite.title})
        )
    return patched_for, patched_against


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
