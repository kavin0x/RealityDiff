from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from realitydiff.claim import Claim
from realitydiff.diffing import diff_states
from realitydiff.models import Commit, WatchResult
from realitydiff.reasoner import Reasoner
from realitydiff.store import BeliefStore
from realitydiff.xai import LanguageModel, XAIClient

log = logging.getLogger("realitydiff.service")


class RealityDiff:
    def __init__(self, store: BeliefStore, model: LanguageModel | None = None) -> None:
        self.store = store
        self.model = model or XAIClient()
        self.reasoner = Reasoner(self.model)
        self._watch_locks: dict[str, threading.Lock] = {}
        self._watch_meta = threading.Lock()

    @classmethod
    def open(cls, path: str | Path, model: LanguageModel | None = None) -> "RealityDiff":
        return cls(BeliefStore(path), model=model)

    def claim(self, claim_id: str) -> Claim:
        record = self.store.get_claim(claim_id)
        if record is None:
            raise KeyError(claim_id)
        return Claim(self.store, record)

    def open_claim(self, statement: str, *, author: str = "reasoner") -> tuple[Claim, Commit]:
        statement = statement.strip()
        if len(statement) < 3:
            raise ValueError("Claim is too short")
        record = self.store.create_claim(statement)
        try:
            state, reason, meta = self.reasoner.initialize(statement, conv_id=record.id)
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
            self.store.set_model_cache(
                record.id,
                response_id=meta.get("response_id"),
                compaction=meta.get("compaction"),
            )
            return self.claim(record.id), commit
        except Exception:
            self.store.delete_claim(record.id)
            raise

    def watch(self, claim_id: str, *, author: str = "watcher") -> WatchResult:
        lock = self._lock_for(claim_id)
        if not lock.acquire(blocking=False):
            return WatchResult(
                claim_id=claim_id,
                changed=False,
                in_progress=True,
                detail="Watch already running; UI stays usable.",
            )
        try:
            living = self.claim(claim_id)
            head = living.head
            if head is None:
                raise RuntimeError("Claim has no commits yet")
            compaction = None
            if living.record.compaction_json:
                try:
                    compaction = json.loads(living.record.compaction_json)
                except json.JSONDecodeError:
                    compaction = None
            new_state, reason, changed, meta = self.reasoner.update(
                living.record.title,
                head.state,
                conv_id=living.id,
                previous_response_id=living.record.last_response_id,
                compaction=compaction,
            )
            self.store.mark_watched(claim_id)
            self.store.set_model_cache(
                claim_id,
                response_id=meta.get("response_id"),
                compaction=meta.get("compaction") or compaction,
            )
            cached = int(meta.get("cached_tokens") or 0)
            if not changed:
                return WatchResult(
                    claim_id=claim_id,
                    changed=False,
                    detail=reason,
                    cached_tokens=cached,
                )
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
            return WatchResult(
                claim_id=claim_id,
                changed=True,
                commit=commit,
                diff=diff,
                detail=reason,
                cached_tokens=cached,
            )
        finally:
            lock.release()

    def watch_due(self, *, author: str = "watcher") -> list[WatchResult]:
        results: list[WatchResult] = []
        now = time.time()
        for record in self.store.list_claims():
            if not record.watching or not record.head:
                continue
            last = record.last_watched_at.timestamp() if record.last_watched_at else 0.0
            if now - last < record.watch_interval_seconds:
                continue
            try:
                results.append(self.watch(record.id, author=author))
            except Exception:
                log.exception("watch failed for %s", record.id)
                self.store.mark_watched(record.id)
        return results

    def _lock_for(self, claim_id: str) -> threading.Lock:
        with self._watch_meta:
            return self._watch_locks.setdefault(claim_id, threading.Lock())
