"""Fan-out the same question to several provider+model pairs."""

from __future__ import annotations

import asyncio
import math
import os
import threading
import time
from collections import deque
from typing import Any

from realitydiff.providers import (
    DEFAULT_CHAT_TIMEOUT,
    MAX_CHAT_TIMEOUT,
    MAX_COMPARE_MODELS,
    PROVIDERS,
    ProviderError,
    make_chat_provider,
    safe_error_message,
)
from realitydiff.store import BeliefStore

COMPARE_CONCURRENCY = 3
DEFAULT_CALLS_PER_MINUTE = 30
MAX_CALLS_PER_MINUTE = 600
RATE_WINDOW_SECONDS = 60.0


class CompareRateLimited(Exception):
    """Too many compare model calls in the last minute."""

    def __init__(self, retry_after: int, limit: int) -> None:
        self.retry_after = max(1, int(retry_after))
        self.limit = limit
        super().__init__(
            f"Compare limit reached: at most {limit} model calls per minute. "
            f"Try again in {self.retry_after}s."
        )


def calls_per_minute() -> int:
    raw = (os.environ.get("REALITYDIFF_COMPARE_PER_MINUTE") or "").strip()
    if not raw:
        return DEFAULT_CALLS_PER_MINUTE
    try:
        number = int(raw)
    except ValueError:
        return DEFAULT_CALLS_PER_MINUTE
    return max(1, min(MAX_CALLS_PER_MINUTE, number))


def model_allowlist() -> tuple[set[tuple[str, str]], set[str]] | None:
    """Parse REALITYDIFF_COMPARE_MODELS. Unset or blank means any model.

    Entries are comma separated. ``provider:model`` pins a provider; a bare
    model id is allowed on any provider. OpenRouter ids can contain a colon
    (``foo/bar:free``), so the prefix only counts as a provider when it is one.
    """
    raw = (os.environ.get("REALITYDIFF_COMPARE_MODELS") or "").strip()
    if not raw:
        return None
    pinned: set[tuple[str, str]] = set()
    bare: set[str] = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        prefix, sep, rest = entry.partition(":")
        if sep and prefix.strip().lower() in PROVIDERS and rest.strip():
            pinned.add((prefix.strip().lower(), rest.strip()))
        else:
            bare.add(entry)
    if not pinned and not bare:
        return None
    return pinned, bare


def check_allowlist(targets: list[dict[str, str]]) -> None:
    allowed = model_allowlist()
    if allowed is None:
        return
    pinned, bare = allowed
    blocked = [
        f"{item['provider']}:{item['model']}"
        for item in targets
        if (item["provider"], item["model"]) not in pinned and item["model"] not in bare
    ]
    if blocked:
        raise ValueError(
            "Model not in REALITYDIFF_COMPARE_MODELS: " + ", ".join(blocked)[:200]
        )


class CompareLimits:
    """Shared ceiling for compare: a concurrency cap and a per-minute call budget."""

    def __init__(
        self,
        *,
        concurrency: int = COMPARE_CONCURRENCY,
        per_minute: int | None = None,
        clock=time.monotonic,
    ) -> None:
        self.concurrency = max(1, int(concurrency))
        self._per_minute = per_minute
        self._clock = clock
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()
        self._semaphore: asyncio.Semaphore | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def per_minute(self) -> int:
        return self._per_minute if self._per_minute is not None else calls_per_minute()

    def semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._loop is not loop:
            self._semaphore = asyncio.Semaphore(self.concurrency)
            self._loop = loop
        return self._semaphore

    def reserve(self, count: int) -> None:
        """Book ``count`` model calls in the window or raise CompareRateLimited."""
        limit = self.per_minute
        with self._lock:
            now = self._clock()
            while self._calls and now - self._calls[0] >= RATE_WINDOW_SECONDS:
                self._calls.popleft()
            if len(self._calls) + count > limit:
                if count > limit or not self._calls:
                    wait = RATE_WINDOW_SECONDS
                else:
                    # Oldest booking that has to expire before this request fits.
                    index = len(self._calls) + count - limit - 1
                    wait = RATE_WINDOW_SECONDS - (now - self._calls[index])
                raise CompareRateLimited(math.ceil(wait), limit)
            self._calls.extend([now] * count)


_default_limits = CompareLimits()


def normalize_targets(raw: list[Any]) -> list[dict[str, str]]:
    if not raw:
        raise ValueError("Pick at least one model")
    if len(raw) > MAX_COMPARE_MODELS:
        raise ValueError(f"At most {MAX_COMPARE_MODELS} models per request")
    targets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if isinstance(item, str):
            provider, _, model = item.partition(":")
            item = {"provider": provider, "model": model}
        if not isinstance(item, dict):
            raise ValueError("Each model must be {provider, model}")
        provider = str(item.get("provider") or "").strip().lower()
        model = str(item.get("model") or "").strip()
        if provider not in PROVIDERS:
            raise ValueError("Provider must be xai or openrouter")
        if not model:
            raise ValueError("Model id is required")
        key = (provider, model)
        if key in seen:
            continue
        seen.add(key)
        targets.append({"provider": provider, "model": model})
    if not targets:
        raise ValueError("Pick at least one model")
    return targets


def clamp_timeout(value: float | None) -> float:
    if value is None:
        return DEFAULT_CHAT_TIMEOUT
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_CHAT_TIMEOUT
    return max(1.0, min(MAX_CHAT_TIMEOUT, number))


async def run_compare(
    question: str,
    targets: list[dict[str, str]],
    *,
    timeout: float | None = None,
    store: BeliefStore | None = None,
    factory=None,
    limits: CompareLimits | None = None,
) -> dict[str, Any]:
    factory = factory or make_chat_provider
    limits = limits or _default_limits
    question = (question or "").strip()
    if len(question) < 3:
        raise ValueError("Question is too short")
    pairs = normalize_targets(targets)
    check_allowlist(pairs)
    limit = clamp_timeout(timeout)
    limits.reserve(len(pairs))
    gate = limits.semaphore()

    async def gated(item: dict[str, str]) -> dict[str, Any]:
        async with gate:
            return await _one(question, item["provider"], item["model"], limit, factory)

    results = await asyncio.gather(*[gated(item) for item in pairs])
    payload = {
        "question": question,
        "timeout_seconds": limit,
        "results": list(results),
    }
    if store is not None:
        record = store.save_compare_run(question, list(results))
        payload["id"] = record["id"]
        payload["created_at"] = record["created_at"]
    return payload


async def _one(question: str, provider: str, model: str, timeout: float, factory) -> dict[str, Any]:
    row: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "status": "pending",
        "answer": None,
        "error": None,
        "latency_ms": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "cost": None,
    }
    try:
        client = factory(provider, model=model, timeout=timeout)
        result = await client.achat(question, model=model, timeout=timeout)
        row.update(
            {
                "status": "ok",
                "model": result.model,
                "answer": result.text,
                "latency_ms": result.latency_ms,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.total_tokens,
                "cost": result.cost,
            }
        )
    except ProviderError as exc:
        row["status"] = "error"
        row["error"] = safe_error_message(exc, fallback="Request failed")
    except Exception as exc:
        row["status"] = "error"
        row["error"] = safe_error_message(exc, fallback="Request failed")
    return row
