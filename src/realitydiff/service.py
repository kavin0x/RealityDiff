from __future__ import annotations

from pathlib import Path

from realitydiff.claim import Claim
from realitydiff.diffing import diff_states
from realitydiff.models import Commit, WatchResult
from realitydiff.reasoner import Reasoner
from realitydiff.store import BeliefStore
from realitydiff.xai import LanguageModel, XAIClient


class RealityDiff:
    def __init__(self, store: BeliefStore, model: LanguageModel | None = None) -> None:
        self.store = store
        self.model = model or XAIClient()
        self.reasoner = Reasoner(self.model)

    @classmethod
    def open(cls, path: str | Path, model: LanguageModel | None = None) -> "RealityDiff":
        return cls(BeliefStore(path), model=model)

    def claim(self, claim_id: str) -> Claim:
        record = self.store.get_claim(claim_id)
        if record is None:
            raise KeyError(claim_id)
        return Claim(self.store, record)

    def open_claim(self, statement: str, *, author: str = "reasoner") -> tuple[Claim, Commit]:
        record = self.store.create_claim(statement)
        state, reason = self.reasoner.initialize(statement)
        commit = Commit.create(
            claim_id=record.id,
            parent_id=None,
            state=state,
            author=author,
            message="Initial belief",
            reason=reason,
            previous_confidence=None,
        )
        self.store.append_commit(commit)
        return self.claim(record.id), commit

    def watch(self, claim_id: str, *, author: str = "watcher") -> WatchResult:
        living = self.claim(claim_id)
        head = living.head
        if head is None:
            raise RuntimeError("Claim has no commits yet")
        new_state, reason, changed = self.reasoner.update(living.record.title, head.state)
        self.store.mark_watched(claim_id)
        if not changed:
            return WatchResult(claim_id=claim_id, changed=False, detail=reason)
        self.store.set_working_state(claim_id, new_state)
        commit = living.commit(
            "Watch update",
            author=author,
            reason=reason,
        )
        diff = diff_states(
            head.state,
            commit.state,
            from_id=head.id,
            to_id=commit.id,
            reason=reason,
        )
        return WatchResult(claim_id=claim_id, changed=True, commit=commit, diff=diff, detail=reason)
