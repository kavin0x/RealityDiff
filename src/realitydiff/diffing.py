from __future__ import annotations

from typing import Any

from realitydiff.models import ClaimDiff, ClaimState, Evidence, FieldChange, Prediction, Unknown


def _index_evidence(items: list[Evidence]) -> dict[str, Evidence]:
    return {item.id: item for item in items}


def _index_unknowns(items: list[Unknown]) -> dict[str, Unknown]:
    return {item.id: item for item in items}


def _index_predictions(items: list[Prediction]) -> dict[str, Prediction]:
    return {item.id: item for item in items}


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def diff_states(
    before: ClaimState | None,
    after: ClaimState | None,
    *,
    from_id: str | None = None,
    to_id: str | None = None,
    reason: str = "",
) -> ClaimDiff:
    changes: list[FieldChange] = []
    prev_conf = before.confidence if before else None
    new_conf = after.confidence if after else None

    if before is None and after is None:
        return ClaimDiff(from_id=from_id, to_id=to_id)

    if before is None and after is not None:
        changes.append(
            FieldChange(path="statement", before=None, after=after.statement, kind="added")
        )
        return ClaimDiff(
            from_id=from_id,
            to_id=to_id,
            previous_confidence=None,
            new_confidence=after.confidence,
            confidence_delta=after.confidence,
            reason=reason or "Initial belief state.",
            changes=changes
            + [
                FieldChange(path=f"evidence_for/{item.id}", before=None, after=_dump(item), kind="added")
                for item in after.evidence_for
            ]
            + [
                FieldChange(
                    path=f"evidence_against/{item.id}", before=None, after=_dump(item), kind="added"
                )
                for item in after.evidence_against
            ]
            + [
                FieldChange(path=f"unknowns/{item.id}", before=None, after=_dump(item), kind="added")
                for item in after.unknowns
            ]
            + [
                FieldChange(
                    path=f"predictions/{item.id}", before=None, after=_dump(item), kind="added"
                )
                for item in after.predictions
            ],
        )

    if before is not None and after is None:
        return ClaimDiff(
            from_id=from_id,
            to_id=to_id,
            previous_confidence=before.confidence,
            new_confidence=None,
            confidence_delta=-before.confidence,
            reason=reason or "Belief deleted.",
            changes=[FieldChange(path="state", before=_dump(before), after=None, kind="removed")],
        )

    assert before is not None and after is not None

    for field in ("statement", "refined_statement", "summary"):
        left = getattr(before, field)
        right = getattr(after, field)
        if left != right:
            changes.append(
                FieldChange(
                    path=field,
                    before=left,
                    after=right,
                    kind="changed" if left and right else ("added" if right else "removed"),
                )
            )

    if before.confidence != after.confidence:
        changes.append(
            FieldChange(
                path="confidence",
                before=before.confidence,
                after=after.confidence,
                kind="changed",
            )
        )

    for bucket in ("evidence_for", "evidence_against"):
        left_map = _index_evidence(getattr(before, bucket))
        right_map = _index_evidence(getattr(after, bucket))
        for key in sorted(set(left_map) | set(right_map)):
            if key not in right_map:
                changes.append(
                    FieldChange(
                        path=f"{bucket}/{key}",
                        before=_dump(left_map[key]),
                        after=None,
                        kind="removed",
                    )
                )
            elif key not in left_map:
                changes.append(
                    FieldChange(
                        path=f"{bucket}/{key}",
                        before=None,
                        after=_dump(right_map[key]),
                        kind="added",
                    )
                )
            elif left_map[key].model_dump(mode="json") != right_map[key].model_dump(mode="json"):
                changes.append(
                    FieldChange(
                        path=f"{bucket}/{key}",
                        before=_dump(left_map[key]),
                        after=_dump(right_map[key]),
                        kind="changed",
                    )
                )

    left_u = _index_unknowns(before.unknowns)
    right_u = _index_unknowns(after.unknowns)
    for key in sorted(set(left_u) | set(right_u)):
        if key not in right_u:
            changes.append(
                FieldChange(path=f"unknowns/{key}", before=_dump(left_u[key]), after=None, kind="removed")
            )
        elif key not in left_u:
            changes.append(
                FieldChange(path=f"unknowns/{key}", before=None, after=_dump(right_u[key]), kind="added")
            )
        elif left_u[key].model_dump(mode="json") != right_u[key].model_dump(mode="json"):
            changes.append(
                FieldChange(
                    path=f"unknowns/{key}",
                    before=_dump(left_u[key]),
                    after=_dump(right_u[key]),
                    kind="changed",
                )
            )

    left_p = _index_predictions(before.predictions)
    right_p = _index_predictions(after.predictions)
    for key in sorted(set(left_p) | set(right_p)):
        if key not in right_p:
            changes.append(
                FieldChange(
                    path=f"predictions/{key}", before=_dump(left_p[key]), after=None, kind="removed"
                )
            )
        elif key not in left_p:
            changes.append(
                FieldChange(
                    path=f"predictions/{key}", before=None, after=_dump(right_p[key]), kind="added"
                )
            )
        elif left_p[key].model_dump(mode="json") != right_p[key].model_dump(mode="json"):
            changes.append(
                FieldChange(
                    path=f"predictions/{key}",
                    before=_dump(left_p[key]),
                    after=_dump(right_p[key]),
                    kind="changed",
                )
            )

    delta = round(after.confidence - before.confidence, 1)
    return ClaimDiff(
        from_id=from_id,
        to_id=to_id,
        previous_confidence=before.confidence,
        new_confidence=after.confidence,
        confidence_delta=delta,
        reason=reason,
        changes=changes,
    )
