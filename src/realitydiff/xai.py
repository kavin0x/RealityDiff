from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Protocol

import httpx


DEFAULT_MODELS = (
    "grok-4.3",  # 1M context window; cheapest cached-input path among current Grok 4.x
    "grok-4.5",
    "grok-4.20-0309-non-reasoning",
    "grok-4.6",
)

JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
TRAILING_COMMA = re.compile(r",(\s*[}\]])")
COMPACT_AFTER_TOKENS = 6_000


class XAIError(RuntimeError):
    pass


class LanguageModel(Protocol):
    def complete(
        self,
        prompt: str,
        *,
        search: bool = False,
        system: str | None = None,
        conv_id: str | None = None,
        cache_key: str | None = None,
        previous_response_id: str | None = None,
        compaction: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class XAIClient:
    """xAI Responses API with live web_search / x_search, prompt cache, then fallback."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = "https://api.x.ai/v1",
        models: tuple[str, ...] = DEFAULT_MODELS,
        timeout: float = 180.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("XAI_API_KEY", "")
        if not self.api_key:
            raise XAIError("XAI_API_KEY is not set")
        self.base_url = base_url.rstrip("/")
        self.models = models
        self.timeout = timeout
        self.last_model: str | None = None

    def complete(
        self,
        prompt: str,
        *,
        search: bool = False,
        system: str | None = None,
        conv_id: str | None = None,
        cache_key: str | None = None,
        previous_response_id: str | None = None,
        compaction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        sticky = conv_id or cache_key or "realitydiff"
        attempts = _request_plans(previous_response_id=previous_response_id, compaction=compaction)
        for model in self.models:
            for plan in attempts:
                for retry in range(3):
                    try:
                        payload = _build_payload(
                            model=model,
                            prompt=prompt,
                            system=system,
                            search=search,
                            cache_key=sticky,
                            previous_response_id=plan.get("previous_response_id"),
                            compaction=plan.get("compaction"),
                            store=plan["store"],
                        )
                        headers = {
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                            "x-grok-conv-id": sticky,
                        }
                        response = httpx.post(
                            f"{self.base_url}/responses",
                            headers=headers,
                            json=payload,
                            timeout=self.timeout,
                        )
                        if response.status_code == 429:
                            last_error = XAIError(response.text)
                            time.sleep(2**retry)
                            continue
                        if response.status_code >= 400:
                            last_error = XAIError(f"{model} HTTP {response.status_code}: {response.text[:500]}")
                            break
                        body = response.json()
                        if body.get("error"):
                            last_error = XAIError(str(body.get("error")))
                            break
                        if body.get("status") == "failed":
                            last_error = XAIError(str(body.get("incomplete_details") or body))
                            time.sleep(2**retry)
                            continue
                        text = extract_output_text(body)
                        if not text.strip():
                            last_error = XAIError(f"{model} returned empty output")
                            time.sleep(2**retry)
                            continue
                        self.last_model = model
                        result = {
                            "model": model,
                            "text": text,
                            "citations": extract_citations(body),
                            "response_id": body.get("id"),
                            "cached_tokens": _cached_tokens(body),
                            "input_tokens": _input_tokens(body),
                            "compaction": None,
                            "raw": body,
                        }
                        if result["input_tokens"] >= COMPACT_AFTER_TOKENS:
                            result["compaction"] = self._compact(model, payload["input"], sticky)
                        return result
                    except httpx.HTTPError as exc:
                        last_error = exc
                        time.sleep(2**retry)
        raise XAIError(f"xAI request failed: {last_error}")

    def _compact(self, model: str, messages: list[dict[str, Any]], conv_id: str) -> dict[str, Any] | None:
        try:
            response = httpx.post(
                f"{self.base_url}/responses/compact",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "x-grok-conv-id": conv_id,
                },
                json={"model": model, "input": messages},
                timeout=self.timeout,
            )
            if response.status_code >= 400:
                return None
            body = response.json()
            items = body.get("output") or []
            if items and items[0].get("type") == "compaction" and items[0].get("encrypted_content"):
                return items[0]
        except httpx.HTTPError:
            return None
        return None


def _request_plans(
    *,
    previous_response_id: str | None,
    compaction: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    if previous_response_id:
        plans.append({"store": True, "previous_response_id": previous_response_id, "compaction": None})
    if compaction and compaction.get("encrypted_content"):
        plans.append({"store": True, "previous_response_id": None, "compaction": compaction})
    plans.append({"store": True, "previous_response_id": None, "compaction": None})
    plans.append({"store": False, "previous_response_id": None, "compaction": None})
    # Deduplicate identical plans while preserving order.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for plan in plans:
        key = json.dumps(
            {
                "store": plan["store"],
                "prev": plan.get("previous_response_id"),
                "cmp": bool(plan.get("compaction")),
            },
            sort_keys=True,
        )
        if key not in seen:
            seen.add(key)
            unique.append(plan)
    return unique


def _build_payload(
    *,
    model: str,
    prompt: str,
    system: str | None,
    search: bool,
    cache_key: str,
    previous_response_id: str | None,
    compaction: dict[str, Any] | None,
    store: bool,
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if previous_response_id:
        messages.append({"role": "user", "content": prompt})
    elif compaction:
        messages.append(compaction)
        messages.append({"role": "user", "content": prompt})
    else:
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
    payload: dict[str, Any] = {
        "model": model,
        "store": store,
        "input": messages,
        "prompt_cache_key": cache_key,
    }
    if previous_response_id:
        payload["previous_response_id"] = previous_response_id
    if search:
        payload["tools"] = [{"type": "web_search"}, {"type": "x_search"}]
    return payload


def _cached_tokens(body: dict[str, Any]) -> int:
    usage = body.get("usage") or {}
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    return int(details.get("cached_tokens") or 0)


def _input_tokens(body: dict[str, Any]) -> int:
    usage = body.get("usage") or {}
    return int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)


def extract_output_text(body: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in body.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") in {"output_text", "text"} and part.get("text"):
                chunks.append(part["text"])
    if chunks:
        return "\n".join(chunks)
    if body.get("output_text"):
        return str(body["output_text"])
    return ""


def extract_citations(body: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(url: str, title: str = "") -> None:
        if url and url not in seen:
            seen.add(url)
            found.append({"url": url, "title": title})

    for item in body.get("output") or []:
        for part in item.get("content") or []:
            for annotation in part.get("annotations") or []:
                url = annotation.get("url") or annotation.get("href") or ""
                title = annotation.get("title") or annotation.get("text") or ""
                add(url, title)
            if part.get("type") in {"citation", "url_citation"}:
                add(part.get("url") or "", part.get("title") or "")
        citations = item.get("citations") or []
        if isinstance(citations, list):
            for citation in citations:
                if isinstance(citation, str):
                    add(citation)
                elif isinstance(citation, dict):
                    add(citation.get("url") or citation.get("href") or "", citation.get("title") or "")

    for citation in body.get("citations") or []:
        if isinstance(citation, str):
            add(citation)
        elif isinstance(citation, dict):
            add(citation.get("url") or "", citation.get("title") or "")
    return found


def parse_json_object(text: str) -> dict[str, Any]:
    if not text or not str(text).strip():
        raise XAIError("Model returned empty output")
    cleaned = (
        str(text)
        .strip()
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    blobs: list[str] = [m.group(1).strip() for m in JSON_FENCE.finditer(cleaned)]
    blobs.extend(_json_blobs(cleaned))
    blobs.append(cleaned)
    seen: set[str] = set()
    last_error: Exception | None = None
    for blob in blobs:
        if not blob or blob in seen:
            continue
        seen.add(blob)
        for variant in (blob, TRAILING_COMMA.sub(r"\1", blob)):
            try:
                data = json.loads(variant)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(data, dict):
                return data
    raise XAIError(f"Model did not return JSON: {cleaned[:400]}") from last_error


def _json_blobs(text: str) -> list[str]:
    decoder = json.JSONDecoder()
    blobs: list[str] = []
    idx = 0
    while idx < len(text):
        start = text.find("{", idx)
        if start < 0:
            break
        try:
            _, end = decoder.raw_decode(text, start)
            blobs.append(text[start:end])
            idx = end
        except json.JSONDecodeError:
            idx = start + 1
    return blobs
