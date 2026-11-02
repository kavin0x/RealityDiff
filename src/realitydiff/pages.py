"""Conditional fetches of evidence URLs the belief already cites.

The first successful read is a baseline. Later reads are changes only when
the body hash moves, or when the server answers 200 instead of 304.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BODY = 262_144
FETCH_TIMEOUT = 5.0
_TITLE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
_BLOCK = re.compile(r"(?is)<(script|style)\b[^>]*>.*?</\1>")
_TAG = re.compile(r"(?s)<[^>]+>")
_SPACE = re.compile(r"\s+")


class PageFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class PageSnapshot:
    url: str
    changed: bool
    excerpt: str = ""
    title: str = ""
    etag: str | None = None
    last_modified: str | None = None
    body_hash: str = ""


class HttpPageFetcher:
    def __init__(self, *, transport: httpx.BaseTransport | None = None, timeout: float = FETCH_TIMEOUT) -> None:
        self._transport = transport
        self.timeout = timeout

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        body_hash: str | None = None,
    ) -> PageSnapshot:
        return self._fetch(url, etag=etag, last_modified=last_modified, body_hash=body_hash, hops=0)

    def _fetch(
        self,
        url: str,
        *,
        etag: str | None,
        last_modified: str | None,
        body_hash: str | None,
        hops: int,
    ) -> PageSnapshot:
        _require_public_http(url)
        headers = {
            "User-Agent": "RealityDiff",
            "Accept": "text/html, text/plain;q=0.9",
        }
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport, follow_redirects=False) as client:
                response = client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise PageFetchError("Fetch failed") from exc
        if response.status_code in {301, 302, 303, 307, 308} and hops < 1:
            location = response.headers.get("location") or ""
            if not location:
                raise PageFetchError(f"HTTP {response.status_code}")
            return self._fetch(
                urljoin(url, location),
                etag=None,
                last_modified=None,
                body_hash=body_hash,
                hops=hops + 1,
            )
        new_etag = response.headers.get("etag") or etag
        new_modified = response.headers.get("last-modified") or last_modified
        if response.status_code == 304:
            return PageSnapshot(
                url=url,
                changed=False,
                etag=new_etag,
                last_modified=new_modified,
                body_hash=body_hash or "",
            )
        if response.status_code != 200:
            raise PageFetchError(f"HTTP {response.status_code}")
        raw = response.content[:MAX_BODY]
        digest = hashlib.sha256(raw).hexdigest()
        title, excerpt = _excerpt(raw)
        changed = bool(body_hash) and digest != body_hash
        return PageSnapshot(
            url=url,
            changed=changed,
            excerpt=excerpt if changed else "",
            title=title if changed else "",
            etag=new_etag,
            last_modified=new_modified,
            body_hash=digest,
        )


def _require_public_http(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise PageFetchError("Unsupported URL")
    host = parts.hostname.strip().lower().rstrip(".")
    if host in {"localhost"} or host.endswith(".local") or host.endswith(".localhost"):
        raise PageFetchError("Unsupported URL")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise PageFetchError("Unsupported URL") from exc
        addresses = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
        if not addresses:
            raise PageFetchError("Unsupported URL")
    for ip in addresses:
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise PageFetchError("Unsupported URL")


def _excerpt(raw: bytes) -> tuple[str, str]:
    text = raw.decode("utf-8", errors="replace")
    title_match = _TITLE.search(text)
    title = _plain(title_match.group(1))[:180] if title_match else ""
    text = _BLOCK.sub(" ", text)
    text = _TAG.sub(" ", text)
    return title, _plain(text)[:1200]


def _plain(text: str) -> str:
    return _SPACE.sub(" ", html.unescape(text)).strip()
