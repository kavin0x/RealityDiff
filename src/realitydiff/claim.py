from __future__ import annotations

from realitydiff.blame import blame_history
from realitydiff.diffing import diff_states
from realitydiff.models import BlameReport, ClaimDiff, ClaimRecord, ClaimState, Commit
from realitydiff.store import BeliefStore


class Claim:
    """Living claim with git-like operations: commit, diff, blame, revert."""

    def __init__(self, store: BeliefStore, record: ClaimRecord) -> None:
        self._store = store
        self.id = record.id
        self.record = record

    def refresh(self) -> ClaimRecord:
        record = self._store.get_claim(self.id)
        if record is None:
            raise KeyError(self.id)
        self.record = record
        return record

    @property
    def head(self) -> Commit | None:
        return self._store.head_commit(self.id)

    @property
    def working(self) -> ClaimState | None:
        return self._store.working_state(self.id)

    def checkout(self) -> ClaimState:
        state = self.working or (self.head.state if self.head else None)
        if state is None:
            raise RuntimeError("Claim has no state yet")
        return state

    def commit(self, message: str, *, author: str = "user", reason: str = "") -> Commit:
        working = self._store.working_state(self.id)
        head = self._store.head_commit(self.id)
        if working is None:
            if head is None:
                raise RuntimeError("Nothing to commit")
            working = head.state
        if head and working.tree_hash() == head.tree_hash:
            raise RuntimeError("Working tree clean")
        commit = Commit.create(
            claim_id=self.id,
            parent_id=head.id if head else None,
            state=working,
            author=author,
            message=message,
            reason=reason,
            previous_confidence=head.state.confidence if head else None,
        )
        return self._store.append_commit(commit)

    def diff(self, a: str | None = None, b: str | None = None) -> ClaimDiff:
        if a is None and b is None:
            head = self.head
            working = self.working
            if head is None:
                return diff_states(None, working, to_id="WORKING")
            return diff_states(head.state, working, from_id=head.id, to_id="WORKING")
        left = self._resolve(a) if a else None
        right = self._resolve(b) if b else self.head
        return diff_states(
            left.state if left else None,
            right.state if right else None,
            from_id=left.id if left else None,
            to_id=right.id if right else None,
            reason=(right.reason if right else ""),
        )

    def log(self) -> list[Commit]:
        return self._store.log(self.id)

    def blame(self) -> BlameReport:
        return blame_history(self.id, self.log())

    def revert(self, sha: str, *, author: str = "user") -> Commit:
        target = self._store.get_commit(sha, claim_id=self.id)
        if target is None:
            raise KeyError(f"Unknown commit {sha}")
        head = self.head
        if head is None:
            raise RuntimeError("Nothing to revert")
        if target.tree_hash == head.tree_hash:
            raise RuntimeError("HEAD already matches that commit")
        commit = Commit.create(
            claim_id=self.id,
            parent_id=head.id,
            state=target.state,
            author=author,
            message=f"Revert to {sha[:12]}",
            reason=f"Restored belief snapshot {sha[:12]}. Previous confidence {head.state.confidence}% → {target.state.confidence}%.",
            previous_confidence=head.state.confidence,
        )
        return self._store.append_commit(commit)

    def _resolve(self, ref: str) -> Commit | None:
        if ref.upper() in {"HEAD", "WORKING"}:
            return self.head
        return self._store.get_commit(ref, claim_id=self.id)
