"""Model providers for compare (and a thin OpenAI-compatible HTTP client).

xAI claim watching still uses ``XAIClient`` in ``xai.py``. This module is the
shared chat path: OpenRouter, plus xAI's OpenAI-compatible chat completions
endpoint so compare does not copy-paste HTTP.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"
DEFAULT_CHAT_TIMEOUT = 45.0
MAX_CHAT_TIMEOUT = 120.0
MAX_COMPARE_MODELS = 6
PROVIDERS = ("xai", "openrouter")

_BEARER = re.compile(r"(Bearer )\S+", re.IGNORECASE)
_KEYISH = re.compile(r"(?i)(?:sk-or-v1-|sk-proj-|xai-)[A-Za-z0-9._\-]+")


class ProviderError(RuntimeError):
    pass


def redact_secrets(text: str, *secrets: str) -> str:
    """Strip API keys from anything we might show, log, or raise."""
    out = str(text)
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[redacted]")
    out = _BEARER.sub(r"\1[redacted]", out)
    out = _KEYISH.sub("[redacted]", out)
    return out


def safe_error_message(exc: BaseException, *secrets: str, fallback: str = "Request failed") -> str:
    raw = redact_secrets(str(exc) or fallback, *secrets)
    raw = raw.replace("\n", " ").strip() or fallback
    return raw[:180]


def default_xai_models() -> tuple[str, ...]:
    override = (os.environ.get("XAI_MODEL") or "").strip()
    if override:
        return (override,)
    from realitydiff.xai import DEFAULT_MODELS

    return DEFAULT_MODELS


def default_openrouter_model() -> str:
    return (os.environ.get("OPENROUTER_MODEL") or DEFAULT_OPENROUTER_MODEL).strip()


def provider_key(name: str) -> str:
    if name == "xai":
        return os.environ.get("XAI_API_KEY", "")
    if name == "openrouter":
        return os.environ.get("OPENROUTER_API_KEY", "")
    return ""


@dataclass
class ChatResult:
    provider: str
    model: str
    text: str
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost: float | None = None


class ChatProvider(Protocol):
    name: str

    def chat(self, prompt: str, *, model: str | None = None, timeout: float | None = None) -> ChatResult: ...

    async def achat(self, prompt: str, *, model: str | None = None, timeout: float | None = None) -> ChatResult: ...


class OpenAICompatClient:
    """POST chat/completions against a fixed URL. No caller-supplied base URL."""

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        url: str,
        default_model: str,
        extra_headers: dict[str, str] | None = None,
        timeout: float = DEFAULT_CHAT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if name not in PROVIDERS:
            raise ProviderError(f"Unknown provider {name}")
        if not api_key:
            raise ProviderError(_missing_key_message(name))
        if url not in {OPENROUTER_CHAT_URL, XAI_CHAT_URL}:
            raise ProviderError("Refusing unknown chat URL")
        self.name = name
        self._api_key = api_key
        self._url = url
        self.default_model = default_model
        self._extra_headers = extra_headers or {}
        self.timeout = timeout
        self._transport = transport
        self._async_transport = async_transport if async_transport is not None else transport

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            **self._extra_headers,
        }
        return headers

    def _model(self, model: str | None) -> str:
        chosen = (model or self.default_model or "").strip()
        if not chosen:
            raise ProviderError("Model id is required")
        return chosen

    def chat(self, prompt: str, *, model: str | None = None, timeout: float | None = None) -> ChatResult:
        chosen = self._model(model)
        limit = _clamp_timeout(timeout if timeout is not None else self.timeout)
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=limit, transport=self._transport) as client:
                response = client.post(
                    self._url,
                    headers=self._headers(),
                    json=_chat_body(chosen, prompt),
                )
            return _parse_chat_response(
                provider=self.name,
                requested_model=chosen,
                response=response,
                latency_ms=_ms(started),
                secret=self._api_key,
            )
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError("Timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(safe_error_message(exc, self._api_key)) from exc

    async def achat(self, prompt: str, *, model: str | None = None, timeout: float | None = None) -> ChatResult:
        chosen = self._model(model)
        limit = _clamp_timeout(timeout if timeout is not None else self.timeout)
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=limit, transport=self._async_transport) as client:
                response = await client.post(
                    self._url,
                    headers=self._headers(),
                    json=_chat_body(chosen, prompt),
                )
            return _parse_chat_response(
                provider=self.name,
                requested_model=chosen,
                response=response,
                latency_ms=_ms(started),
                secret=self._api_key,
            )
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError("Timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(safe_error_message(exc, self._api_key)) from exc


def make_chat_provider(
    name: str,
    *,
    model: str | None = None,
    timeout: float = DEFAULT_CHAT_TIMEOUT,
    api_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
    async_transport: httpx.AsyncBaseTransport | None = None,
) -> OpenAICompatClient:
    name = name.strip().lower()
    if name == "openrouter":
        return OpenAICompatClient(
            name="openrouter",
            api_key=api_key if api_key is not None else provider_key("openrouter"),
            url=OPENROUTER_CHAT_URL,
            default_model=model or default_openrouter_model(),
            extra_headers={
                "HTTP-Referer": "https://github.com/kavin0x/RealityDiff",
                "X-OpenRouter-Title": "Reality Diff",
            },
            timeout=timeout,
            transport=transport,
            async_transport=async_transport,
        )
    if name == "xai":
        return OpenAICompatClient(
            name="xai",
            api_key=api_key if api_key is not None else provider_key("xai"),
            url=XAI_CHAT_URL,
            default_model=model or default_xai_models()[0],
            timeout=timeout,
            transport=transport,
            async_transport=async_transport,
        )
    raise ProviderError("Unknown provider (use xai or openrouter)")


def _missing_key_message(name: str) -> str:
    if name == "openrouter":
        return "OPENROUTER_API_KEY is not set"
    return "XAI_API_KEY is not set"


def _clamp_timeout(value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = DEFAULT_CHAT_TIMEOUT
    return max(1.0, min(MAX_CHAT_TIMEOUT, number))


def _ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


def _chat_body(model: str, prompt: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }


def _parse_chat_response(
    *,
    provider: str,
    requested_model: str,
    response: httpx.Response,
    latency_ms: int,
    secret: str,
) -> ChatResult:
    if response.status_code >= 400:
        snippet = redact_secrets((response.text or "")[:200], secret)
        raise ProviderError(f"HTTP {response.status_code}: {snippet}".strip())
    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderError("Provider returned non-JSON") from exc
    if not isinstance(body, dict):
        raise ProviderError("Provider returned an unexpected body")
    err = body.get("error")
    if err:
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise ProviderError(redact_secrets(str(message or "Provider error"), secret)[:180])
    choices = body.get("choices") or []
    text = ""
    if choices and isinstance(choices[0], dict):
        choice = choices[0]
        if isinstance(choice.get("error"), dict):
            raise ProviderError(
                redact_secrets(str(choice["error"].get("message") or "Provider error"), secret)[:180]
            )
        text = _choice_text(choice)
    if not str(text).strip():
        raise ProviderError("Model returned empty output")
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    used_model = str(body.get("model") or requested_model)
    return ChatResult(
        provider=provider,
        model=used_model,
        text=str(text).strip(),
        latency_ms=latency_ms,
        prompt_tokens=_maybe_int(usage.get("prompt_tokens")),
        completion_tokens=_maybe_int(usage.get("completion_tokens")),
        total_tokens=_maybe_int(usage.get("total_tokens")),
        cost=_maybe_float(usage.get("cost")),
    )


def _choice_text(choice: dict[str, Any]) -> str:
    message = choice.get("message") or {}
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("text"):
                parts.append(str(part["text"]))
        return "".join(parts)
    if choice.get("text"):
        return str(choice["text"])
    return ""


def _maybe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
