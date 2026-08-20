from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Protocol

import httpx


DEFAULT_MODELS = (
    "grok-4.5",
    "grok-4.3",
    "grok-4.20-0309-non-reasoning",
    "grok-4.6",
)

JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class XAIError(RuntimeError):
    pass


class LanguageModel(Protocol):
    def complete(self, prompt: str, *, search: bool = False) -> dict[str, Any]: ...


class XAIClient:
    """xAI Responses API with live web_search / x_search tools."""

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

    def complete(self, prompt: str, *, search: bool = False) -> dict[str, Any]:
        last_error: Exception | None = None
        for model in self.models:
            for attempt in range(3):
                try:
                    payload: dict[str, Any] = {
                        "model": model,
                        "store": False,
                        "input": [{"role": "user", "content": prompt}],
                    }
                    if search:
                        payload["tools"] = [{"type": "web_search"}, {"type": "x_search"}]
                    response = httpx.post(
                        f"{self.base_url}/responses",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        timeout=self.timeout,
                    )
                    if response.status_code == 429:
                        last_error = XAIError(response.text)
                        time.sleep(2 ** attempt)
                        continue
                    if response.status_code >= 400:
                        last_error = XAIError(f"{model} HTTP {response.status_code}: {response.text[:500]}")
                        break
                    body = response.json()
                    text = extract_output_text(body)
                    citations = extract_citations(body)
                    self.last_model = model
                    return {
                        "model": model,
                        "text": text,
                        "citations": citations,
                        "raw": body,
                    }
                except httpx.HTTPError as exc:
                    last_error = exc
                    time.sleep(2 ** attempt)
            # try next model
        raise XAIError(f"xAI request failed: {last_error}")


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
    text = text.strip()
    match = JSON_BLOCK.search(text)
    candidate = match.group(1) if match else None
    if candidate is None:
        match = JSON_OBJECT.search(text)
        candidate = match.group(0) if match else text
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise XAIError(f"Model did not return JSON: {text[:400]}") from exc
    if not isinstance(data, dict):
        raise XAIError("Model JSON was not an object")
    return data
