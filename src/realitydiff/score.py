"""Deterministic confidence ledger.

The model classifies evidence and may nudge the result by a few points.
The stored percentage is this function, not the model's number.
"""

from __future__ import annotations

from realitydiff.models import Evidence, ScoreCard, ScoreStep

PRIOR = 50.0
ADJUSTMENT_LIMIT = 8.0

SUPPORT_KINDS = frozenset(
    {
        "official_confirmation",
        "primary_support",
        "sibling_cadence",
        "reputable_report",
        "rumor",
    }
)
AGAINST_KINDS = frozenset(
    {
        "official_denial",
        "primary_contradiction",
        "reputable_contradiction",
        "rumor_against",
    }
)
# Counted in full for every independent entity. They do not enter the diminishing stack.
ALWAYS_FULL = {
    "official_confirmation": 40.0,
    "official_denial": -45.0,
    "primary_contradiction": -45.0,
}
STACKED = {
    "primary_support": 22.0,
    "sibling_cadence": 18.0,
    "reputable_report": 12.0,
    "rumor": 6.0,
    "reputable_contradiction": -12.0,
    "rumor_against": -6.0,
}
KIND_LABEL = {
    "official_confirmation": "official confirmation",
    "primary_support": "primary support",
    "sibling_cadence": "sibling cadence",
    "reputable_report": "reputable report",
    "rumor": "rumor",
    "official_denial": "official denial",
    "primary_contradiction": "primary contradiction",
    "reputable_contradiction": "reputable contradiction",
    "rumor_against": "rumor against",
    "absence": "absence",
}
KIND_WEIGHT = {
    "official_confirmation": 1.0,
    "official_denial": 1.0,
    "primary_contradiction": 0.92,
    "primary_support": 0.85,
    "sibling_cadence": 0.72,
    "reputable_report": 0.6,
    "reputable_contradiction": 0.55,
    "rumor": 0.35,
    "rumor_against": 0.3,
    "absence": 0.0,
}
HARD_AGAINST = frozenset({"official_denial", "primary_contradiction"})
REPUTABLE_OR_HARDER_AGAINST = HARD_AGAINST | {"reputable_contradiction"}
STRONG_SUPPORT = frozenset(
    {"official_confirmation", "primary_support", "sibling_cadence", "reputable_report"}
)


def weight_for(kind: str) -> float:
    return KIND_WEIGHT.get(kind, 0.35)


def normalize_kind(kind: str | None, *, side: str) -> str | None:
    """Map a model tag onto the closed set. ``absence`` scores nothing and is dropped."""
    text = (kind or "").strip().lower().replace(" ", "_").replace("-", "_")
    if text == "absence":
        return None
    if text in SUPPORT_KINDS or text in AGAINST_KINDS:
        return text
    return "rumor_against" if side == "against" else "rumor"


def score_evidence(
    support: list[Evidence],
    against: list[Evidence],
    *,
    adjustment: float = 0.0,
    adjustment_why: str = "",
) -> ScoreCard:
    support_items = _collapse(support, "for")
    against_items = _collapse(against, "against")
    steps = _steps(support_items) + _steps(against_items)
    nudge = _clamp_adjustment(adjustment)
    raw = PRIOR + sum(step.points for step in steps) + nudge
    caps: list[str] = []
    floor = _floor(support_items, against_items)
    if floor is not None and raw < floor:
        caps.append(f"floor {int(floor)}")
        raw = floor
    ceiling = _ceiling(support_items, against_items)
    if raw > ceiling:
        caps.append(f"cap {int(ceiling)}")
        raw = ceiling
    confidence = round(max(0.0, min(100.0, raw)), 1)
    return ScoreCard(
        prior=PRIOR,
        steps=steps,
        adjustment=nudge,
        adjustment_why=adjustment_why.strip(),
        caps=caps,
        confidence=confidence,
    )


def close_score(card: ScoreCard | None, verdict: str, *, confidence: float) -> ScoreCard:
    """Record a resolved claim on the ledger without reopening the arithmetic."""
    base = card or ScoreCard(confidence=confidence)
    label = f"closed {verdict}"
    caps = [item for item in base.caps if not item.startswith("closed ")]
    caps.append(label)
    return base.model_copy(update={"caps": caps, "confidence": round(confidence, 1)})


def _clamp_adjustment(value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return round(max(-ADJUSTMENT_LIMIT, min(ADJUSTMENT_LIMIT, number)), 1)


def _collapse(items: list[Evidence], side: str) -> list[Evidence]:
    """One contribution per named entity: its strongest tag on this side."""
    best: dict[str, Evidence] = {}
    order: list[str] = []
    solo = 0
    for item in items:
        if item.kind == "absence":
            continue
        kind = item.kind if item.kind in SUPPORT_KINDS or item.kind in AGAINST_KINDS else ""
        if not kind:
            kind = "rumor_against" if side == "against" else "rumor"
        entity = (item.entity or "").strip().lower()
        if entity:
            key = entity
        else:
            solo += 1
            key = f"__item_{solo}"
        current = item.model_copy(update={"kind": kind, "entity": entity})
        previous = best.get(key)
        if previous is None:
            best[key] = current
            order.append(key)
            continue
        if _magnitude(current.kind) > _magnitude(previous.kind):
            best[key] = current
    return [best[key] for key in order]


def _magnitude(kind: str) -> float:
    if kind in ALWAYS_FULL:
        return abs(ALWAYS_FULL[kind])
    return abs(STACKED.get(kind, 0.0))


def _steps(items: list[Evidence]) -> list[ScoreStep]:
    full = [item for item in items if item.kind in ALWAYS_FULL]
    stacked = [item for item in items if item.kind in STACKED]
    full.sort(key=lambda item: _magnitude(item.kind), reverse=True)
    stacked.sort(key=lambda item: _magnitude(item.kind), reverse=True)
    steps: list[ScoreStep] = []
    for item in full:
        points = ALWAYS_FULL[item.kind]
        steps.append(ScoreStep(label=KIND_LABEL[item.kind], points=points, kind=item.kind))
    for index, item in enumerate(stacked):
        points = round(STACKED[item.kind] * (0.5**index), 1)
        if points == 0:
            continue
        steps.append(ScoreStep(label=KIND_LABEL[item.kind], points=points, kind=item.kind))
    return steps


def _floor(support: list[Evidence], against: list[Evidence]) -> float | None:
    sibling = any(item.kind == "sibling_cadence" for item in support)
    blocked = any(item.kind in REPUTABLE_OR_HARDER_AGAINST for item in against)
    if sibling and not blocked:
        return 60.0
    return None


def _ceiling(support: list[Evidence], against: list[Evidence]) -> float:
    official = any(item.kind == "official_confirmation" for item in support)
    hard_against = any(item.kind in HARD_AGAINST for item in against)
    if hard_against and not official:
        return 15.0
    strong = any(item.kind in STRONG_SUPPORT for item in support)
    if not strong:
        return 65.0
    if not official:
        return 85.0
    return 100.0
