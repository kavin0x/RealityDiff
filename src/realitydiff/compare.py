"""Fan-out the same question to several provider+model pairs."""

from __future__ import annotations

import asyncio
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
    factory=make_chat_provider,
) -> dict[str, Any]:
    question = (question or "").strip()
    if len(question) < 3:
        raise ValueError("Question is too short")
    pairs = normalize_targets(targets)
    limit = clamp_timeout(timeout)
    results = await asyncio.gather(
        *[
            _one(question, item["provider"], item["model"], limit, factory)
            for item in pairs
        ]
    )
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
