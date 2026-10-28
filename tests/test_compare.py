import asyncio

import pytest
from fastapi.testclient import TestClient

from realitydiff.api import create_app
from realitydiff.compare import MAX_COMPARE_MODELS, run_compare
from realitydiff.providers import ChatResult, ProviderError
from realitydiff.service import RealityDiff
from realitydiff.store import BeliefStore
from tests.test_reasoner import INIT, ScriptedModel


@pytest.fixture(autouse=True)
def no_live_provider_keys(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    for name in ("REALITYDIFF_MAX_TOKENS", "REALITYDIFF_COMPARE_MODELS", "REALITYDIFF_COMPARE_PER_MINUTE"):
        monkeypatch.delenv(name, raising=False)


def _app(tmp_path):
    engine = RealityDiff(BeliefStore(tmp_path / "db.sqlite"), model=ScriptedModel([INIT]))
    return create_app(engine, start_watcher=False)


class ScriptedChat:
    def __init__(self, name, model, **kw):
        self.name = name
        self.model = model

    async def achat(self, prompt, *, model=None, timeout=None):
        chosen = model or self.model
        if chosen == "fail-me":
            raise ProviderError("HTTP 500: upstream exploded sk-or-v1-SHOULD-NOT-LEAK")
        if chosen == "slow-me":
            raise ProviderError("Timed out")
        return ChatResult(
            provider=self.name,
            model=chosen,
            text=f"{self.name}:{chosen} says {prompt[:12]}",
            latency_ms=12,
            prompt_tokens=3,
            completion_tokens=5,
            total_tokens=8,
            cost=0.001 if self.name == "openrouter" else None,
        )


def test_compare_one_failure_does_not_fail_the_others(tmp_path, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("realitydiff.compare.make_chat_provider", ScriptedChat)
    with TestClient(_app(tmp_path), base_url="http://127.0.0.1:8000") as client:
        res = client.post(
            "/api/compare",
            json={
                "question": "Is the sky blue?",
                "models": [
                    {"provider": "xai", "model": "grok-4.3"},
                    {"provider": "openrouter", "model": "fail-me"},
                ],
            },
        )
        assert res.status_code == 200
        body = res.json()
        by_model = {item["model"]: item for item in body["results"]}
        assert by_model["grok-4.3"]["status"] == "ok"
        assert "xai:grok-4.3 says" in by_model["grok-4.3"]["answer"]
        assert by_model["fail-me"]["status"] == "error"
        assert by_model["fail-me"]["answer"] is None
        assert "sk-or-v1" not in (by_model["fail-me"]["error"] or "")
        assert "SHOULD-NOT-LEAK" not in res.text
        history = client.get("/api/compare/history").json()
        assert history[0]["id"] == body["id"]
        assert history[0]["question"] == "Is the sky blue?"


def test_compare_timeout_is_isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("realitydiff.compare.make_chat_provider", ScriptedChat)
    with TestClient(_app(tmp_path), base_url="http://127.0.0.1:8000") as client:
        res = client.post(
            "/api/compare",
            json={
                "question": "Ping both models",
                "models": [
                    {"provider": "xai", "model": "slow-me"},
                    {"provider": "openrouter", "model": "openai/gpt-4o-mini"},
                ],
                "timeout_seconds": 2,
            },
        )
        assert res.status_code == 200
        by_model = {item["model"]: item for item in res.json()["results"]}
        assert by_model["slow-me"]["status"] == "error"
        assert by_model["slow-me"]["error"] == "Timed out"
        assert by_model["openai/gpt-4o-mini"]["status"] == "ok"


def test_compare_caps_model_count(tmp_path, monkeypatch):
    monkeypatch.setattr("realitydiff.compare.make_chat_provider", ScriptedChat)
    models = [{"provider": "xai", "model": f"m{i}"} for i in range(MAX_COMPARE_MODELS + 1)]
    with TestClient(_app(tmp_path), base_url="http://127.0.0.1:8000") as client:
        res = client.post("/api/compare", json={"question": "too many", "models": models})
        assert res.status_code == 400
        assert "At most" in res.json()["detail"]


def test_smoke_localhost_compare_with_mocked_providers(tmp_path, monkeypatch):
    import threading
    import time

    import httpx
    import uvicorn

    monkeypatch.setattr("realitydiff.compare.make_chat_provider", ScriptedChat)
    app = _app(tmp_path)
    config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                health = httpx.get("http://127.0.0.1:8765/api/health", timeout=0.5)
                if health.status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            raise AssertionError("server did not start on 127.0.0.1:8765")
        home = httpx.get("http://127.0.0.1:8765/", timeout=5)
        assert home.status_code == 200
        assert b"compare-view" in home.content
        res = httpx.post(
            "http://127.0.0.1:8765/api/compare",
            json={
                "question": "Smoke the mocked providers",
                "models": [
                    {"provider": "xai", "model": "grok-4.3"},
                    {"provider": "openrouter", "model": "fail-me"},
                ],
            },
            timeout=10,
        )
        assert res.status_code == 200
        statuses = {item["model"]: item["status"] for item in res.json()["results"]}
        assert statuses["grok-4.3"] == "ok"
        assert statuses["fail-me"] == "error"
        assert "sk-or-v1" not in res.text
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_run_compare_does_not_need_store():
    async def go():
        return await run_compare(
            "hello there",
            [{"provider": "xai", "model": "grok-4.3"}],
            factory=ScriptedChat,
        )

    payload = asyncio.run(go())
    assert payload["results"][0]["status"] == "ok"
    assert "id" not in payload


def test_engine_starts_without_xai_key(tmp_path, monkeypatch):
    import pytest

    from realitydiff.service import RealityDiff
    from realitydiff.xai import XAIError

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    engine = RealityDiff.open(tmp_path / "rd.db")
    with pytest.raises(XAIError):
        engine.open_claim("Apple will replace Siri with an LLM.")
