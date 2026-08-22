from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from realitydiff.hashing import object_id, stable_item_id


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Evidence(BaseModel):
    id: str
    statement: str
    source_url: str | None = None
    source_title: str | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    weight: float = 0.5
    notes: str = ""

    @field_validator("weight")
    @classmethod
    def clamp_weight(cls, value: float) -> float:
        return max(0.0, min(1.0, value))

    @classmethod
    def make(
        cls,
        statement: str,
        *,
        source_url: str | None = None,
        source_title: str | None = None,
        weight: float = 0.5,
        notes: str = "",
        retrieved_at: datetime | None = None,
    ) -> "Evidence":
        return cls(
            id=stable_item_id("ev", source_url or "", statement),
            statement=statement,
            source_url=source_url,
            source_title=source_title,
            weight=weight,
            notes=notes,
            retrieved_at=retrieved_at or utcnow(),
        )


class Unknown(BaseModel):
    id: str
    question: str
    why_it_matters: str = ""

    @classmethod
    def make(cls, question: str, why_it_matters: str = "") -> "Unknown":
        return cls(
            id=stable_item_id("uk", question),
            question=question,
            why_it_matters=why_it_matters,
        )


class Citation(BaseModel):
    url: str
    title: str = ""

    @classmethod
    def make(cls, url: str, title: str = "") -> "Citation":
        return cls(url=url.strip(), title=(title or "").strip())


class Prediction(BaseModel):
    id: str
    statement: str
    due: str | None = None
    status: Literal["open", "confirmed", "falsified"] = "open"
    how_to_falsify: str = ""

    @classmethod
    def make(
        cls,
        statement: str,
        *,
        due: str | None = None,
        status: Literal["open", "confirmed", "falsified"] = "open",
        how_to_falsify: str = "",
    ) -> "Prediction":
        return cls(
            id=stable_item_id("pr", statement),
            statement=statement,
            due=due,
            status=status,
            how_to_falsify=how_to_falsify,
        )


class ClaimState(BaseModel):
    statement: str
    refined_statement: str = ""
    confidence: float = 50.0
    evidence_for: list[Evidence] = Field(default_factory=list)
    evidence_against: list[Evidence] = Field(default_factory=list)
    unknowns: list[Unknown] = Field(default_factory=list)
    predictions: list[Prediction] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    summary: str = ""

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, value: float) -> float:
        return round(max(0.0, min(100.0, value)), 1)

    def tree_hash(self) -> str:
        data = self.model_dump(mode="json")
        for bucket in ("evidence_for", "evidence_against"):
            for item in data.get(bucket) or []:
                item.pop("retrieved_at", None)
        return object_id("tree", data)

    def all_evidence(self) -> list[tuple[str, Evidence]]:
        return [("for", item) for item in self.evidence_for] + [
            ("against", item) for item in self.evidence_against
        ]


class Commit(BaseModel):
    id: str
    claim_id: str
    parent_id: str | None = None
    tree_hash: str
    created_at: datetime
    author: str
    message: str
    reason: str = ""
    previous_confidence: float | None = None
    new_confidence: float
    state: ClaimState

    @classmethod
    def create(
        cls,
        *,
        claim_id: str,
        parent_id: str | None,
        state: ClaimState,
        author: str,
        message: str,
        reason: str = "",
        previous_confidence: float | None = None,
        created_at: datetime | None = None,
    ) -> "Commit":
        created = created_at or utcnow()
        tree_hash = state.tree_hash()
        payload = {
            "author": author,
            "claim_id": claim_id,
            "created_at": created.isoformat(),
            "message": message,
            "parent": parent_id,
            "reason": reason,
            "tree": tree_hash,
        }
        return cls(
            id=object_id("commit", payload),
            claim_id=claim_id,
            parent_id=parent_id,
            tree_hash=tree_hash,
            created_at=created,
            author=author,
            message=message,
            reason=reason,
            previous_confidence=previous_confidence,
            new_confidence=state.confidence,
            state=state,
        )


class ClaimRecord(BaseModel):
    id: str
    created_at: datetime
    title: str
    watching: bool = True
    watch_interval_seconds: int = 300
    head: str | None = None
    last_watched_at: datetime | None = None
    last_response_id: str | None = None
    compaction_json: str | None = None


class FieldChange(BaseModel):
    path: str
    before: Any = None
    after: Any = None
    kind: Literal["added", "removed", "changed"]


class ClaimDiff(BaseModel):
    from_id: str | None
    to_id: str | None
    previous_confidence: float | None
    new_confidence: float | None
    confidence_delta: float = 0.0
    reason: str = ""
    changes: list[FieldChange] = Field(default_factory=list)


class BlameEntry(BaseModel):
    path: str
    item_id: str | None = None
    introduced_in: str
    introduced_at: datetime
    author: str
    message: str
    last_changed_in: str
    last_changed_at: datetime
    last_author: str
    last_message: str
    value: Any = None


class BlameReport(BaseModel):
    claim_id: str
    head: str | None
    entries: list[BlameEntry] = Field(default_factory=list)


class WatchResult(BaseModel):
    claim_id: str
    changed: bool
    commit: Commit | None = None
    diff: ClaimDiff | None = None
    detail: str = ""
    in_progress: bool = False
    cached_tokens: int = 0
