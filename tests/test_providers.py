import logging

import httpx
import pytest

from realitydiff.providers import (
    OPENROUTER_CHAT_URL,
    XAI_CHAT_URL,
    OpenAICompatClient,
    ProviderError,
    make_chat_provider,
    redact_secrets,
)


SECRET = "sk-or-v1-SUPER-SECRET-KEY-do-not-leak"


def _response(json=None, status=200, text=""):
    def handler(request: httpx.Request) -> httpx.Response:
        handler.calls.append(request)
        if json is not None:
            return httpx.Response(status, json=json)
        return httpx.Response(status, text=text)

    handler.calls = []
    return handler


def _client(handler, **kw):
    return OpenAICompatClient(
        name="openrouter",
        api_key=SECRET,
        url=OPENROUTER_CHAT_URL,
        default_model="openai/gpt-4o-mini",
        extra_headers={"HTTP-Referer": "https://github.com/kavin0x/RealityDiff", "X-OpenRouter-Title": "Reality Diff"},
        transport=httpx.MockTransport(handler),
        **kw,
    )


def test_openrouter_async_chat_uses_the_same_url():
    handler = _response(
        {
            "choices": [{"message": {"content": "async hi"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )
    import asyncio

    result = asyncio.run(_client(handler).achat("hi"))
    assert result.text == "async hi"
    assert str(handler.calls[0].url) == OPENROUTER_CHAT_URL


def test_openrouter_posts_fixed_url_headers_and_usage():
    handler = _response(
        {
            "id": "gen-1",
            "model": "openai/gpt-4o-mini",
            "choices": [{"message": {"role": "assistant", "content": "Hello there"}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
                "cost": 0.00014,
            },
        }
    )
    result = _client(handler).chat("What is the meaning of life?")
    assert result.text == "Hello there"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 4
    assert result.total_tokens == 14
    assert result.cost == pytest.approx(0.00014)
    req = handler.calls[0]
    assert str(req.url) == OPENROUTER_CHAT_URL
    assert req.headers["authorization"] == f"Bearer {SECRET}"
    assert req.headers["http-referer"] == "https://github.com/kavin0x/RealityDiff"
    assert req.headers["x-openrouter-title"] == "Reality Diff"
    assert b"What is the meaning of life?" in req.content


def test_http_error_redacts_key_from_exception_and_logs(caplog):
    caplog.set_level(logging.DEBUG)
    handler = _response(status=401, text=f"invalid api key {SECRET} Bearer {SECRET}")
    with pytest.raises(ProviderError) as exc:
        _client(handler).chat("hi")
    message = str(exc.value)
    assert SECRET not in message
    assert "401" in message
    assert "[redacted]" in message
    assert SECRET not in caplog.text


def test_timeout_is_a_short_safe_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out talking to host", request=request)

    with pytest.raises(ProviderError) as exc:
        _client(handler).chat("hi", timeout=1)
    assert str(exc.value) == "Timed out"
    assert SECRET not in str(exc.value)


def test_refuses_unknown_url():
    with pytest.raises(ProviderError, match="unknown chat URL"):
        OpenAICompatClient(
            name="openrouter",
            api_key=SECRET,
            url="https://evil.example/api",
            default_model="x",
        )


def test_make_chat_provider_uses_env_and_fixed_urls(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4")
    monkeypatch.setenv("XAI_API_KEY", "xai-secret")
    openrouter = make_chat_provider("openrouter")
    xai = make_chat_provider("xai", model="grok-4.3")
    assert openrouter._url == OPENROUTER_CHAT_URL
    assert xai._url == XAI_CHAT_URL
    assert openrouter.default_model == "anthropic/claude-sonnet-4"
    assert xai.default_model == "grok-4.3"


def test_redact_secrets_strips_bearer_tokens():
    text = redact_secrets(f"Authorization: Bearer {SECRET} extra {SECRET}", SECRET)
    assert SECRET not in text
    assert "Bearer [redacted]" in text
