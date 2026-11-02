from datetime import timedelta

import pytest

from realitydiff.decisions import Decision
from realitydiff.models import utcnow
from realitydiff.pages import PageSnapshot
from realitydiff.providers import ProviderError
from realitydiff.service import RealityDiff
from realitydiff.store import BeliefStore
from tests.test_reasoner import INIT, WATCH_NOOP, ScriptedModel


class ScriptFetcher:
    def __init__(self, snapshots: dict[str, PageSnapshot]) -> None:
        self.snapshots = snapshots
        self.calls: list[str] = []

    def fetch(self, url, *, etag=None, last_modified=None, body_hash=None):
        self.calls.append(url)
        return self.snapshots[url]


class ScriptDecider:
    def __init__(self, decision) -> None:
        self.decision = decision
        self.calls: list[dict] = []

    def decide(self, state):
        self.calls.append(state)
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision


def _engine(tmp_path, monkeypatch, *, decision, snapshots, payloads=None):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    model = ScriptedModel(payloads or [INIT])
    engine = RealityDiff(
        BeliefStore(tmp_path / "db.sqlite"),
        model=model,
        decider=ScriptDecider(decision),
        fetcher=ScriptFetcher(snapshots),
    )
    return engine, model


def _open(engine):
    living, _ = engine.open_claim("Apple is going to replace Siri with an LLM.")
    engine.store.mark_watched(living.id)
    return living


def _snaps(changed_url=None, excerpt=""):
    urls = ("https://example.com/bloomberg", "https://example.com/ios")
    snaps = {}
    for url in urls:
        changed = url == changed_url
        snaps[url] = PageSnapshot(
            url=url,
            changed=changed,
            excerpt=excerpt if changed else "",
            title="Story" if changed else "",
            body_hash="hash",
        )
    return snaps


def test_unchanged_page_skips_decision_and_reasoner(tmp_path, monkeypatch):
    engine, model = _engine(tmp_path, monkeypatch, decision=Decision(0.9, "rumor", 0.8, "nudge"), snapshots=_snaps())
    living = _open(engine)
    watched = living.refresh().last_watched_at
    calls = len(model.calls)
    assert engine.watch_due() == []
    record = living.refresh()
    assert record.last_watched_at == watched
    assert record.last_probed_at is not None
    assert engine._decider.calls == []
    assert len(model.calls) == calls


def test_clear_rumor_nudges_without_moving_slow_clock(tmp_path, monkeypatch):
    decision = Decision(0.91, "rumor", 0.88, "nudge")
    engine, model = _engine(
        tmp_path,
        monkeypatch,
        decision=decision,
        snapshots=_snaps("https://example.com/bloomberg", "A leak says the runtime moved."),
    )
    living = _open(engine)
    watched = living.refresh().last_watched_at
    calls = len(model.calls)
    results = engine.watch_due()
    assert len(results) == 1
    assert results[0].changed is True
    assert results[0].commit.author == "decider"
    assert results[0].commit.message == "Fast update"
    assert results[0].commit.reason == "rumor · 0.91"
    assert results[0].commit.new_confidence == pytest.approx(results[0].commit.previous_confidence + 4)
    record = living.refresh()
    assert record.last_watched_at == watched
    assert record.last_probed_at is not None
    assert len(model.calls) == calls
    signals = engine.store.pending_signals(living.id)
    assert signals and signals[0]["kind"] == "rumor"
    assert "leak" in engine._decider.calls[0]["changed_pages"][0]["excerpt"]


def test_official_signal_runs_reasoner_immediately(tmp_path, monkeypatch):
    decision = Decision(0.95, "official", 0.90, "nudge")
    engine, model = _engine(
        tmp_path,
        monkeypatch,
        decision=decision,
        snapshots=_snaps("https://example.com/bloomberg", "Official docs shipped the runtime."),
        payloads=[INIT, WATCH_NOOP],
    )
    living = _open(engine)
    engine.watch_due()
    assert len(model.calls) == 2
    prompt = model.calls[-1]["prompt"]
    assert "FAST LANE" in prompt
    assert "Official docs shipped" in prompt
    assert model.calls[-1]["search"] is True
    record = living.refresh()
    assert record.last_watched_at is not None
    assert engine.store.pending_signals(living.id) == []


def test_decision_failure_does_not_mark_clocks(tmp_path, monkeypatch):
    engine, model = _engine(
        tmp_path,
        monkeypatch,
        decision=ProviderError("HTTP 500: down"),
        snapshots=_snaps("https://example.com/bloomberg", "moved"),
    )
    living = _open(engine)
    watched = living.refresh().last_watched_at
    calls = len(model.calls)
    assert engine.watch_due() == []
    record = living.refresh()
    assert record.last_probed_at is None
    assert record.last_watched_at == watched
    assert len(model.calls) == calls


def test_without_openrouter_only_the_slow_clock_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    model = ScriptedModel([INIT, WATCH_NOOP])
    decider = ScriptDecider(Decision(0.99, "official", 0.99, "rewrite"))
    engine = RealityDiff(
        BeliefStore(tmp_path / "db.sqlite"),
        model=model,
        decider=decider,
        fetcher=ScriptFetcher(_snaps("https://example.com/bloomberg", "moved")),
    )
    living, _ = engine.open_claim("Apple is going to replace Siri with an LLM.")
    engine.store.set_watching(living.id, True, 60)
    engine.store.mark_watched(living.id)
    assert engine.watch_due() == []
    assert decider.calls == []
    assert len(model.calls) == 1
    engine.store.mark_watched(living.id, when=utcnow() - timedelta(minutes=6))
    engine.watch_due()
    assert len(model.calls) == 2
    assert "FAST LANE" in model.calls[-1]["prompt"]
    assert decider.calls == []
