from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from realitydiff.claim import Claim
from realitydiff.compress import compact_belief
from realitydiff.decisions import Decision, DecisionsClient, confidence_delta, decision_reason, gate
from realitydiff.diffing import diff_states
from realitydiff.models import ClaimState, Commit, WatchResult, utcnow
from realitydiff.pages import HttpPageFetcher
from realitydiff.providers import ProviderError, default_xai_models, provider_key
from realitydiff.reasoner import Reasoner
from realitydiff.store import BeliefStore
from realitydiff.xai import LanguageModel, XAIClient, XAIError

FAST_PROBE_SECONDS = 15
SLOW_WATCH_SECONDS = 300
SERVICE_TICK_SECONDS = 30
MAX_SOURCE_URLS = 6

log = logging.getLogger("realitydiff.service")


def _api_host(base_url: str) -> str:
    if not base_url:
        return ""
    return urlsplit(base_url).hostname or base_url


class _MissingXAIKey:
    """Stand-in so the UI and compare still run with only an OpenRouter key."""

    last_model = None

    def complete(self, *args, **kwargs):
        raise XAIError("XAI_API_KEY is not set. Claims and watch need an xAI key; compare can use OpenRouter alone.")


def _default_model() -> LanguageModel:
    try:
        return XAIClient(models=default_xai_models())
    except XAIError:
        return _MissingXAIKey()


@dataclass
class ProbeOutcome:
    result: WatchResult | None = None
    escalate: bool = False


class RealityDiff:
    def __init__(
        self,
        store: BeliefStore,
        model: LanguageModel | None = None,
        *,
        decider: DecisionsClient | None = None,
        fetcher: HttpPageFetcher | None = None,
    ) -> None:
        self.store = store
        self.model = model or _default_model()
        self.reasoner = Reasoner(self.model)
        self._decider = decider
        self._fetcher = fetcher or HttpPageFetcher()
        self._watch_locks: dict[str, threading.Lock] = {}
        self._watch_meta = threading.Lock()
        self._activity_lock = threading.Lock()
        self._activity: dict[str, dict] = {}

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
        self._begin_research(
            record.id,
            kind="open",
            phase="Surveying the internet for an initial confidence",
            title=statement,
            confidence=None,
        )
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
            self._maybe_close(record.id, state, meta)
            return self.claim(record.id), commit
        except Exception:
            self.store.delete_claim(record.id)
            raise
        finally:
            self._end_research(record.id)

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
            if living.record.closed:
                return WatchResult(
                    claim_id=claim_id,
                    changed=False,
                    closed=True,
                    detail="Claim already closed.",
                )
            head = living.head
            if head is None:
                raise RuntimeError("Claim has no commits yet")
            self._begin_research(
                claim_id,
                kind="watch",
                phase="Searching the web and X to update confidence",
                title=living.record.title,
                confidence=head.state.confidence,
            )
            compaction = None
            if living.record.compaction_json:
                try:
                    compaction = json.loads(living.record.compaction_json)
                except json.JSONDecodeError:
                    compaction = None
            signals = self.store.pending_signals(claim_id)
            new_state, reason, changed, meta = self.reasoner.update(
                living.record.title,
                head.state,
                conv_id=living.id,
                previous_response_id=living.record.last_response_id,
                compaction=compaction,
                fast_lane=format_fast_lane(signals),
            )
            self.store.set_pending_signals(claim_id, [])
            self.store.mark_watched(claim_id)
            self.store.set_model_cache(
                claim_id,
                response_id=meta.get("response_id"),
                compaction=meta.get("compaction") or compaction,
            )
            cached = int(meta.get("cached_tokens") or 0)
            closed = bool(new_state.outcome) or bool(
                isinstance(meta.get("close_claim"), dict) and meta.get("close_claim", {}).get("ok")
            )
            if not changed:
                if closed:
                    self._maybe_close(claim_id, new_state, meta)
                return WatchResult(
                    claim_id=claim_id,
                    changed=False,
                    detail=reason,
                    cached_tokens=cached,
                    closed=bool(self.claim(claim_id).record.closed),
                )
            self.store.set_working_state(claim_id, new_state)
            commit = living.commit(
                "Claim closed" if new_state.outcome else "Watch update",
                author=author,
                reason=reason,
            )
            self._maybe_close(claim_id, commit.state, meta)
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
                closed=bool(commit.state.outcome),
            )
        finally:
            self._end_research(claim_id)
            lock.release()

    def watch_due(self, *, author: str = "watcher") -> list[WatchResult]:
        results: list[WatchResult] = []
        now = time.time()
        decisions_on = self._decisions() is not None
        for record in self.store.list_claims():
            if record.closed or not record.watching or not record.head:
                continue
            escalate = False
            if decisions_on and _clock_due(record.last_probed_at, FAST_PROBE_SECONDS, now):
                outcome = self.probe(record.id)
                escalate = outcome.escalate
                if outcome.result is not None and not escalate:
                    results.append(outcome.result)
            if escalate or _clock_due(record.last_watched_at, SLOW_WATCH_SECONDS, now):
                try:
                    results.append(self.watch(record.id, author=author))
                except Exception:
                    log.exception("watch failed for %s", record.id)
                    self.store.mark_watched(record.id)
        return results

    def probe(self, claim_id: str) -> ProbeOutcome:
        lock = self._lock_for(claim_id)
        if not lock.acquire(blocking=False):
            return ProbeOutcome()
        try:
            return self._probe_locked(claim_id)
        finally:
            lock.release()

    def _probe_locked(self, claim_id: str) -> ProbeOutcome:
        decider = self._decisions()
        if decider is None:
            return ProbeOutcome()
        living = self.claim(claim_id)
        if living.record.closed:
            return ProbeOutcome()
        head = living.head
        if head is None:
            return ProbeOutcome()
        urls = evidence_urls(head.state)
        fingerprints = dict(self.store.source_fingerprints(claim_id))
        if not urls:
            self.store.mark_probed(claim_id)
            return ProbeOutcome()
        changed: list[dict[str, str]] = []
        for url in urls:
            prior = fingerprints.get(url) or {}
            try:
                snap = self._fetcher.fetch(
                    url,
                    etag=prior.get("etag"),
                    last_modified=prior.get("last_modified"),
                    body_hash=prior.get("body_hash"),
                )
            except Exception:
                log.warning("source fetch failed for %s", url)
                continue
            fingerprints[url] = {
                "etag": snap.etag,
                "last_modified": snap.last_modified,
                "body_hash": snap.body_hash or prior.get("body_hash") or "",
            }
            if snap.changed:
                changed.append({"url": snap.url, "title": snap.title, "excerpt": snap.excerpt})
        if not changed:
            self.store.set_source_fingerprints(claim_id, fingerprints)
            self.store.mark_probed(claim_id)
            return ProbeOutcome()
        payload = {
            "claim": living.record.title,
            "belief": compact_belief(head.state),
            "changed_pages": changed,
        }
        try:
            decision = decider.decide(payload)
        except ProviderError:
            log.exception("decision failed for %s", claim_id)
            return ProbeOutcome()
        self.store.set_source_fingerprints(claim_id, fingerprints)
        action = gate(decision)
        if action == "skip":
            self.store.mark_probed(claim_id)
            return ProbeOutcome()
        signal = _signal(decision, action, changed)
        self.store.set_pending_signals(claim_id, [*self.store.pending_signals(claim_id), signal])
        self.store.mark_probed(claim_id)
        if action == "escalate":
            return ProbeOutcome(escalate=True)
        if action != "nudge":
            return ProbeOutcome()
        result = self._nudge(living, head, decision)
        return ProbeOutcome(result=result)

    def _nudge(self, living: Claim, head: Commit, decision: Decision) -> WatchResult | None:
        state = head.state.model_copy(update={"confidence": head.state.confidence + confidence_delta(decision.kind)})
        if state.tree_hash() == head.state.tree_hash():
            return None
        reason = decision_reason(decision)
        self.store.set_working_state(living.id, state)
        commit = living.commit("Fast update", author="decider", reason=reason)
        diff = diff_states(
            head.state,
            commit.state,
            from_id=head.id,
            to_id=commit.id,
            reason=reason,
        )
        return WatchResult(
            claim_id=living.id,
            changed=True,
            commit=commit,
            diff=diff,
            detail=reason,
        )

    def _decisions(self) -> DecisionsClient | None:
        if not provider_key("openrouter"):
            return None
        if self._decider is not None:
            return self._decider
        self._decider = DecisionsClient(api_key=provider_key("openrouter"))
        return self._decider

    def _maybe_close(self, claim_id: str, state, meta: dict | None = None) -> None:
        close = (meta or {}).get("close_claim") if isinstance((meta or {}).get("close_claim"), dict) else None
        outcome = state.outcome
        if outcome is None and not (close and close.get("ok")):
            return
        verdict = outcome.verdict if outcome else close.get("verdict")
        if verdict not in {"confirmed", "falsified", "settled"}:
            return
        self.store.close_claim(claim_id, verdict=verdict, closed_at=outcome.closed_at if outcome else None)

    def research_activity(self) -> list[dict]:
        now = time.time()
        with self._activity_lock:
            jobs = []
            for job in self._activity.values():
                started = float(job["started"])
                route = self.reasoner_route()
                jobs.append(
                    {
                        "claim_id": job["claim_id"],
                        "kind": job["kind"],
                        "phase": job["phase"],
                        "title": job["title"],
                        "confidence": job["confidence"],
                        "model": route["model"],
                        "api": route["api"],
                        "started_at": started,
                        "elapsed_seconds": round(now - started, 1),
                    }
                )
        jobs.sort(key=lambda item: item["started_at"])
        return jobs

    def reasoner_route(self) -> dict[str, str]:
        """Model id and API host the belief reasoner is using right now."""
        client = self.model
        models = tuple(getattr(client, "models", ()) or ())
        active = getattr(client, "active_model", None) or getattr(client, "last_model", None)
        if not active and models:
            active = models[0]
        base = str(getattr(client, "base_url", "") or "")
        return {"model": str(active or ""), "api": _api_host(base)}

    def _begin_research(
        self,
        claim_id: str,
        *,
        kind: str,
        phase: str,
        title: str,
        confidence: float | None,
    ) -> None:
        with self._activity_lock:
            self._activity[claim_id] = {
                "claim_id": claim_id,
                "kind": kind,
                "phase": phase,
                "title": title,
                "confidence": confidence,
                "started": time.time(),
            }

    def _end_research(self, claim_id: str) -> None:
        with self._activity_lock:
            self._activity.pop(claim_id, None)

    def _lock_for(self, claim_id: str) -> threading.Lock:
        with self._watch_meta:
            return self._watch_locks.setdefault(claim_id, threading.Lock())


def format_fast_lane(signals: list[dict]) -> str:
    if not signals:
        return "(none)"
    lines = []
    for item in signals[-8:]:
        material = item.get("material")
        sided = item.get("sided") or ""
        lines.append(
            "- {at} {kind} ({action}){sided} material {material} {url}: {excerpt}".format(
                at=item.get("at") or "",
                kind=item.get("kind") or "",
                action=item.get("action") or "",
                sided=f" sided {sided}" if sided else "",
                material="" if material is None else material,
                url=item.get("url") or "",
                excerpt=str(item.get("excerpt") or "")[:240],
            )
        )
    return "\n".join(lines)


def evidence_urls(state: ClaimState) -> list[str]:
    ranked = sorted(
        [*state.evidence_for, *state.evidence_against],
        key=lambda item: item.weight,
        reverse=True,
    )
    seen: list[str] = []
    for item in ranked:
        url = (item.source_url or "").strip()
        if url.startswith(("http://", "https://")) and url not in seen:
            seen.append(url)
        if len(seen) >= MAX_SOURCE_URLS:
            break
    return seen


def _clock_due(stamp, seconds: int, now: float) -> bool:
    if stamp is None:
        return True
    return now - stamp.timestamp() >= seconds


def _signal(decision: Decision, action: str, pages: list[dict[str, str]]) -> dict:
    return {
        "at": utcnow().isoformat(),
        "action": action,
        "kind": decision.kind,
        "material": round(decision.material, 4),
        "margin": round(decision.margin, 4),
        "size": decision.size,
        "sided": decision.sided,
        "url": pages[0]["url"] if pages else "",
        "excerpt": " ".join(page.get("excerpt") or "" for page in pages)[:400],
        "reason": decision_reason(decision),
    }
