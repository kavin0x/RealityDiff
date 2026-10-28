import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from realitydiff.api import create_app
from realitydiff.compare import (
    COMPARE_CONCURRENCY,
    CompareLimits,
    CompareRateLimited,
    run_compare,
)
from realitydiff.providers import (
    DEFAULT_MAX_TOKENS,
    MAX_MAX_TOKENS,
    MIN_MAX_TOKENS,
    OPENROUTER_CHAT_URL,
    XAI_CHAT_URL,
    ChatResult,
    make_chat_provider,
    max_output_tokens,
)
from realitydiff.service import RealityDiff
from realitydiff.store import BeliefStore
from tests.test_reasoner import INIT, ScriptedModel


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "XAI_API_KEY",
        "OPENROUTER_API_KEY",
        "REALITYDIFF_MAX_TOKENS",
        "REALITYDIFF_COMPARE_MODELS",
        "REALITYDIFF_COMPARE_PER_MINUTE",
    ):
        monkeypatch.delenv(name, raising=False)


def _app(tmp_path):
    engine = RealityDiff(BeliefStore(tmp_path / "db.sqlite"), model=ScriptedModel([INIT]))
    return create_app(engine, start_watcher=False)


class EchoChat:
    def __init__(self, name, model, **kw):
        self.name = name
        self.model = model

    async def achat(self, prompt, *, model=None, timeout=None):
        return ChatResult(provider=self.name, model=model or self.model, text="ok", latency_ms=1)


def _ok_handler(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    return handler


# max_tokens


def test_max_tokens_default_and_clamped(monkeypatch):
    assert max_output_tokens() == DEFAULT_MAX_TOKENS == 1024
    monkeypatch.setenv("REALITYDIFF_MAX_TOKENS", "300")
    assert max_output_tokens() == 300
    monkeypatch.setenv("REALITYDIFF_MAX_TOKENS", "999999")
    assert max_output_tokens() == MAX_MAX_TOKENS
    monkeypatch.setenv("REALITYDIFF_MAX_TOKENS", "1")
    assert max_output_tokens() == MIN_MAX_TOKENS
    monkeypatch.setenv("REALITYDIFF_MAX_TOKENS", "lots")
    assert max_output_tokens() == DEFAULT_MAX_TOKENS


@pytest.mark.parametrize("provider,url", [("openrouter", OPENROUTER_CHAT_URL), ("xai", XAI_CHAT_URL)])
def test_both_providers_send_max_tokens(monkeypatch, provider, url):
    monkeypatch.setenv("REALITYDIFF_MAX_TOKENS", "512")
    seen: list[dict] = []
    transport = httpx.MockTransport(_ok_handler(seen))
    client = make_chat_provider(provider, model="m-1", api_key="fake-key", transport=transport)
    client.chat("hello")
    asyncio.run(client.achat("hello"))
    assert [body["max_tokens"] for body in seen] == [512, 512]
    assert all(body["model"] == "m-1" for body in seen)


def test_default_max_tokens_in_body():
    seen: list[dict] = []
    client = make_chat_provider(
        "openrouter", model="m", api_key="fake-key", transport=httpx.MockTransport(_ok_handler(seen))
    )
    client.chat("hello")
    assert seen[0]["max_tokens"] == 1024


# concurrency


def test_compare_runs_at_most_three_model_calls_at_once():
    state = {"now": 0, "peak": 0}

    class SlowChat:
        def __init__(self, name, model, **kw):
            self.name = name

        async def achat(self, prompt, *, model=None, timeout=None):
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
            await asyncio.sleep(0.02)
            state["now"] -= 1
            return ChatResult(provider=self.name, model=model, text="ok", latency_ms=1)

    async def main():
        limits = CompareLimits(per_minute=100)
        targets = [{"provider": "openrouter", "model": f"m{i}"} for i in range(6)]
        # Two requests at once share the same global gate.
        return await asyncio.gather(
            run_compare("Is it raining?", targets, factory=SlowChat, limits=limits),
            run_compare("Is it sunny?", targets, factory=SlowChat, limits=limits),
        )

    results = asyncio.run(main())
    assert COMPARE_CONCURRENCY == 3
    assert state["peak"] == 3
    assert all(row["status"] == "ok" for payload in results for row in payload["results"])


# per-minute cap


def test_rate_limit_counts_model_calls_and_resets():
    clock = {"t": 1000.0}
    limits = CompareLimits(per_minute=5, clock=lambda: clock["t"])
    limits.reserve(3)
    clock["t"] += 10
    limits.reserve(2)
    with pytest.raises(CompareRateLimited) as info:
        limits.reserve(1)
    assert info.value.retry_after == 50
    assert "5 model calls per minute" in str(info.value)
    clock["t"] += 50
    limits.reserve(3)


def test_rate_limit_rejects_request_bigger_than_cap():
    limits = CompareLimits(per_minute=2)
    with pytest.raises(CompareRateLimited):
        limits.reserve(3)


def test_api_returns_429_with_retry_after(tmp_path, monkeypatch):
    monkeypatch.setenv("REALITYDIFF_COMPARE_PER_MINUTE", "4")
    monkeypatch.setattr("realitydiff.compare.make_chat_provider", EchoChat)
    body = {
        "question": "Is the sky blue?",
        "models": [
            {"provider": "openrouter", "model": "a"},
            {"provider": "openrouter", "model": "b"},
            {"provider": "xai", "model": "c"},
        ],
    }
    with TestClient(_app(tmp_path), base_url="http://127.0.0.1:8000") as client:
        first = client.post("/api/compare", json=body)
        assert first.status_code == 200
        second = client.post("/api/compare", json=body)
        assert second.status_code == 429
        assert "per minute" in second.json()["detail"]
        assert int(second.headers["retry-after"]) >= 1
        # The rejected request booked nothing and stored nothing.
        assert len(client.get("/api/compare/history").json()) == 1


# allowlist


def test_allowlist_unset_allows_any_model(tmp_path, monkeypatch):
    monkeypatch.setattr("realitydiff.compare.make_chat_provider", EchoChat)
    with TestClient(_app(tmp_path), base_url="http://127.0.0.1:8000") as client:
        res = client.post(
            "/api/compare",
            json={"question": "Is the sky blue?", "models": [{"provider": "openrouter", "model": "any/expensive-model"}]},
        )
    assert res.status_code == 200


def test_allowlist_blocks_other_models(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "REALITYDIFF_COMPARE_MODELS",
        "openrouter:openai/gpt-4o-mini, meta-llama/llama-3.1-8b-instruct:free , grok-4.3",
    )
    calls = []

    class CountingChat(EchoChat):
        async def achat(self, prompt, *, model=None, timeout=None):
            calls.append(model)
            return await super().achat(prompt, model=model, timeout=timeout)

    monkeypatch.setattr("realitydiff.compare.make_chat_provider", CountingChat)
    with TestClient(_app(tmp_path), base_url="http://127.0.0.1:8000") as client:
        ok = client.post(
            "/api/compare",
            json={
                "question": "Is the sky blue?",
                "models": [
                    {"provider": "openrouter", "model": "openai/gpt-4o-mini"},
                    {"provider": "openrouter", "model": "meta-llama/llama-3.1-8b-instruct:free"},
                    {"provider": "xai", "model": "grok-4.3"},
                ],
            },
        )
        assert ok.status_code == 200, ok.text
        blocked = client.post(
            "/api/compare",
            json={
                "question": "Is the sky blue?",
                "models": [
                    {"provider": "openrouter", "model": "openai/gpt-4o-mini"},
                    {"provider": "xai", "model": "openai/gpt-4o-mini"},
                    {"provider": "openrouter", "model": "openai/o1-pro"},
                ],
            },
        )
    assert blocked.status_code == 400
    detail = blocked.json()["detail"]
    assert "REALITYDIFF_COMPARE_MODELS" in detail
    assert "openrouter:openai/o1-pro" in detail
    assert "xai:openai/gpt-4o-mini" in detail
    assert len(calls) == 3  # nothing ran for the blocked request


# run id


def test_compare_run_ids_do_not_collide_in_the_same_instant(tmp_path, monkeypatch):
    fixed = datetime(2026, 10, 27, 12, 0, 0, 123456, tzinfo=timezone.utc)
    monkeypatch.setattr("realitydiff.store.utcnow", lambda: fixed)
    store = BeliefStore(tmp_path / "db.sqlite")
    first = store.save_compare_run("same question", [])
    second = store.save_compare_run("same question", [])
    assert first["id"] != second["id"]
    assert first["id"].startswith("cmp_") and second["id"].startswith("cmp_")
    assert len(store.list_compare_runs()) == 2
