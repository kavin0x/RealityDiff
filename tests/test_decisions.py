import json

import httpx
import pytest

from realitydiff.decisions import (
    DEFAULT_DECISION_MODEL,
    FALLBACK_DECISION_MODEL,
    OPENROUTER_DECISIONS_URL,
    Decision,
    DecisionsClient,
    gate,
)
from realitydiff.pages import HttpPageFetcher, PageFetchError
from realitydiff.providers import ProviderError

SECRET = "sk-or-v1-SUPER-SECRET-KEY-do-not-leak"


def _answers(noul=0.91, kind="rumor", probs=None, score=1.1):
    probs = probs or {"rumor": 0.93, "none": 0.04, "contradiction": 0.02, "official": 0.01, "resolved": 0.0}
    return {
        "model": DEFAULT_DECISION_MODEL,
        "answers": {
            "material": {"type": "noul", "noul": noul},
            "kind": {"type": "choice", "choice": kind, "probabilities": probs},
            "size": {"type": "score", "score": score},
        },
    }


def test_request_shape_and_parse():
    def handler(request: httpx.Request) -> httpx.Response:
        handler.calls.append(request)
        return httpx.Response(200, json=_answers())

    handler.calls = []
    client = DecisionsClient(SECRET, transport=httpx.MockTransport(handler))
    decision = client.decide({"claim": "ships soon"})
    body = json.loads(handler.calls[0].content)
    assert str(handler.calls[0].url) == OPENROUTER_DECISIONS_URL
    assert body["model"] == DEFAULT_DECISION_MODEL
    assert set(body["questions"]) == {"material", "kind", "size"}
    assert body["questions"]["material"]["type"] == "noul"
    assert body["questions"]["kind"]["type"] == "choice"
    assert body["questions"]["size"]["type"] == "score"
    assert body["state"]["claim"] == "ships soon"
    assert decision.kind == "rumor"
    assert decision.material == pytest.approx(0.91)
    assert decision.margin == pytest.approx(0.89)
    assert decision.size == "nudge"
    assert gate(decision) == "nudge"


def test_d1_failure_falls_through_to_luna_once():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        handler.models.append(body["model"])
        if len(handler.models) == 1:
            return httpx.Response(404, text=f"missing {SECRET}")
        return httpx.Response(200, json=_answers())

    handler.models = []
    decision = DecisionsClient(SECRET, transport=httpx.MockTransport(handler)).decide({"claim": "x"})
    assert handler.models == [DEFAULT_DECISION_MODEL, FALLBACK_DECISION_MODEL]
    assert decision.kind == "rumor"
    assert SECRET not in decision.model


def test_secret_is_redacted_when_both_models_fail():
    def handler(request: httpx.Request) -> httpx.Response:
        handler.n += 1
        return httpx.Response(529, text=f"down {SECRET}")

    handler.n = 0
    with pytest.raises(ProviderError) as exc:
        DecisionsClient(SECRET, transport=httpx.MockTransport(handler)).decide({"claim": "x"})
    assert handler.n == 2
    assert SECRET not in str(exc.value)
    assert "HTTP 529" in str(exc.value)


def test_http_500_does_not_fall_back():
    def handler(request: httpx.Request) -> httpx.Response:
        handler.n += 1
        return httpx.Response(500, text=f"boom {SECRET}")

    handler.n = 0
    with pytest.raises(ProviderError) as exc:
        DecisionsClient(SECRET, transport=httpx.MockTransport(handler)).decide({"claim": "x"})
    assert handler.n == 1
    assert SECRET not in str(exc.value)


def test_env_override(monkeypatch):
    monkeypatch.setenv("OPENROUTER_DECISION_MODEL", "typesafe/jev-1.13")

    def handler(request: httpx.Request) -> httpx.Response:
        handler.model = json.loads(request.content)["model"]
        return httpx.Response(200, json=_answers())

    handler.model = None
    DecisionsClient(SECRET, transport=httpx.MockTransport(handler)).decide({"claim": "x"})
    assert handler.model == "typesafe/jev-1.13"


def test_gate_thresholds():
    assert gate(Decision(0.91, "rumor", 0.88, "nudge")) == "nudge"
    assert gate(Decision(0.91, "contradiction", 0.80, "nudge")) == "nudge"
    assert gate(Decision(0.91, "rumor", 0.04, "nudge")) == "hold"
    assert gate(Decision(0.40, "rumor", 0.80, "nudge")) == "skip"
    assert gate(Decision(0.95, "official", 0.90, "nudge")) == "escalate"
    assert gate(Decision(0.95, "resolved", 0.90, "unchanged")) == "escalate"
    assert gate(Decision(0.95, "rumor", 0.70, "rewrite")) == "escalate"
    assert gate(Decision(0.20, "none", 0.90, "unchanged")) == "skip"


def test_page_baseline_then_change(monkeypatch):
    monkeypatch.setattr(
        "realitydiff.pages.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("1.1.1.1", 0))],
    )
    seen = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["n"] += 1
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304, headers={"etag": '"v1"'})
        if seen["n"] == 1:
            return httpx.Response(200, text="<title>Note</title><p>hello page</p>", headers={"etag": '"v1"'})
        return httpx.Response(200, text="<p>brand new evidence about the claim</p>", headers={"etag": '"v2"'})

    fetcher = HttpPageFetcher(transport=httpx.MockTransport(handler))
    first = fetcher.fetch("https://example.com/story")
    assert first.changed is False
    assert first.body_hash
    second = fetcher.fetch("https://example.com/story", etag='"v1"', body_hash=first.body_hash)
    assert second.changed is False
    third = fetcher.fetch("https://example.com/story", body_hash=first.body_hash)
    assert third.changed is True
    assert "brand new evidence" in third.excerpt


def test_private_urls_are_refused():
    fetcher = HttpPageFetcher()
    for url in ("http://127.0.0.1/secret", "http://localhost/x", "file:///etc/passwd"):
        with pytest.raises(PageFetchError):
            fetcher.fetch(url)
