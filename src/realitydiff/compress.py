from __future__ import annotations

from typing import Any

from realitydiff.models import ClaimState, Evidence, Prediction

# Ignore retrieved_at / tiny note rewrites when deciding whether a watch is worth a commit.
MATERIAL_CONFIDENCE = 1.0
MATERIAL_WEIGHT = 0.08


def compact_belief(state: ClaimState) -> dict[str, Any]:
    """High-compression snapshot for the model: keep ids, urls, scores; drop timestamps."""
    return {
        "statement": state.statement,
        "refined_statement": state.refined_statement,
        "confidence": state.confidence,
        "summary": _clip(state.summary, 420),
        "evidence_for": [_compact_evidence(item) for item in state.evidence_for],
        "evidence_against": [_compact_evidence(item) for item in state.evidence_against],
        "unknowns": [
            {"id": item.id, "question": _clip(item.question, 180), "why": _clip(item.why_it_matters, 140)}
            for item in state.unknowns
        ],
        "predictions": [_compact_prediction(item) for item in state.predictions],
        "citations": [{"url": c.url, "title": c.title} for c in state.citations[:24]],
    }


def is_material_change(before: ClaimState, after: ClaimState) -> bool:
    if abs(after.confidence - before.confidence) >= MATERIAL_CONFIDENCE:
        return True
    if before.statement.strip() != after.statement.strip():
        return True
    if _evidence_signature(before.evidence_for) != _evidence_signature(after.evidence_for):
        return True
    if _evidence_signature(before.evidence_against) != _evidence_signature(after.evidence_against):
        return True
    if {u.question.strip().lower() for u in before.unknowns} != {u.question.strip().lower() for u in after.unknowns}:
        return True
    if _prediction_signature(before.predictions) != _prediction_signature(after.predictions):
        return True
    return False


def _compact_evidence(item: Evidence) -> dict[str, Any]:
    return {
        "id": item.id,
        "statement": _clip(item.statement, 220),
        "source_url": item.source_url,
        "source_title": item.source_title,
        "weight": round(item.weight, 2),
        "notes": _clip(item.notes, 160),
    }


def _compact_prediction(item: Prediction) -> dict[str, Any]:
    return {
        "id": item.id,
        "statement": _clip(item.statement, 180),
        "due": item.due,
        "status": item.status,
        "how_to_falsify": _clip(item.how_to_falsify, 140),
    }


def _evidence_signature(items: list[Evidence]) -> set[tuple[str, str, str, float]]:
    signed: set[tuple[str, str, str, float]] = set()
    for item in items:
        weight = round(item.weight / MATERIAL_WEIGHT) * MATERIAL_WEIGHT
        signed.add(
            (
                item.id,
                (item.source_url or "").rstrip("/").lower(),
                item.statement.strip().lower()[:160],
                round(weight, 2),
            )
        )
    return signed


def _prediction_signature(items: list[Prediction]) -> set[tuple[str, str]]:
    return {(item.statement.strip().lower()[:160], item.status) for item in items}


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
