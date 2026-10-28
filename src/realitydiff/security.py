"""Small guard for the local web server.

The API has no accounts. By default it only answers requests that are
clearly meant for this machine: the Host header has to be a loopback name,
and a browser write (POST, DELETE, ...) from another origin is refused.
That stops DNS rebinding and cross-site requests from spending API credits
or reading claims.

Binding beyond loopback needs REALITYDIFF_TOKEN. With that bind every
/api/ request has to carry the token, either as ``Authorization: Bearer``
or as ``X-RealityDiff-Token``.
"""

from __future__ import annotations

import hmac
import json
import os
from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
TOKEN_HEADER = "x-realitydiff-token"

BIND_ENV = "REALITYDIFF_BIND_HOST"
TOKEN_ENV = "REALITYDIFF_TOKEN"
ALLOWED_HOSTS_ENV = "REALITYDIFF_ALLOWED_HOSTS"


def normalize_host(value: str) -> str:
    """Lowercase a host name and drop brackets around IPv6."""
    value = value.strip().lower()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return value


def is_loopback(host: str) -> bool:
    return normalize_host(host) in LOOPBACK_HOSTS


def host_from_header(value: str) -> str | None:
    """Host name from a Host header (``name``, ``name:port``, ``[v6]:port``)."""
    value = value.strip()
    if not value:
        return None
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return None
        rest = value[end + 1 :]
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return None
        return normalize_host(value[1:end])
    name, sep, port = value.rpartition(":")
    if sep:
        if not port.isdigit() or ":" in name:
            return None
        value = name
    return normalize_host(value)


def host_from_origin(value: str) -> str | None:
    try:
        parts = urlsplit(value.strip())
        if parts.scheme not in ("http", "https"):
            return None
        return normalize_host(parts.hostname or "") or None
    except ValueError:
        return None


def allowed_hosts(bind_host: str | None = None, extra: str | None = None) -> frozenset[str]:
    hosts = set(LOOPBACK_HOSTS)
    if bind_host and not is_loopback(bind_host) and normalize_host(bind_host) not in ("0.0.0.0", "::", ""):
        hosts.add(normalize_host(bind_host))
    for item in (extra or "").split(","):
        item = normalize_host(item)
        if item:
            hosts.add(item)
    return frozenset(hosts)


def guard_settings(bind_host: str | None = None, token: str | None = None) -> tuple[frozenset[str], str]:
    """Allowed hosts and the token to enforce for a server bound to ``bind_host``.

    On loopback (the default) only loopback Host names pass and no token is
    asked for. On any other bind a token is required, and the bind host plus
    REALITYDIFF_ALLOWED_HOSTS (comma list, ``*`` means any) are allowed too.
    """
    bind_host = bind_host if bind_host is not None else os.environ.get(BIND_ENV, "127.0.0.1")
    if is_loopback(bind_host or "127.0.0.1"):
        return LOOPBACK_HOSTS, ""
    token = server_token() if token is None else token
    if not token:
        raise RuntimeError(bind_check(bind_host, token) or "token required")
    return allowed_hosts(bind_host, os.environ.get(ALLOWED_HOSTS_ENV, "")), token


def server_token() -> str:
    return os.environ.get(TOKEN_ENV, "").strip()


def bind_check(host: str, token: str | None = None) -> str | None:
    """Return an error message if serving on ``host`` is not allowed."""
    token = server_token() if token is None else token
    if is_loopback(host) or token:
        return None
    return (
        f"refusing to serve on {host} without a token. The API has no accounts, so anyone who can "
        "reach the port could read your claims and spend your API credits. Set REALITYDIFF_TOKEN "
        "(env or ~/.config/realitydiff/env) to a long random value, or bind to 127.0.0.1."
    )


class LocalGuard:
    """ASGI middleware: Host check, Origin check on writes, optional token on /api/."""

    def __init__(self, app, *, hosts: frozenset[str], token: str = "") -> None:
        self.app = app
        self.hosts = hosts
        self.token = token.encode("utf-8") if token else b""

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers: dict[str, str] = {}
        for raw_name, raw_value in scope.get("headers") or []:
            name = raw_name.decode("latin-1").lower()
            if name in headers:
                # A request with two Host or Origin headers is not something a browser sends.
                if name in ("host", "origin"):
                    await _deny(scope, send, 400, "Invalid host header")
                    return
                continue
            headers[name] = raw_value.decode("latin-1")

        host = host_from_header(headers.get("host", ""))
        if host is None or (host not in self.hosts and "*" not in self.hosts):
            await _deny(scope, send, 400, "Invalid host header")
            return

        method = scope.get("method", "GET").upper()
        origin = headers.get("origin")
        if origin is not None and method not in SAFE_METHODS:
            origin_host = host_from_origin(origin)
            if origin_host is None or (origin_host not in self.hosts and origin_host != host):
                await _deny(scope, send, 403, "Cross-origin request refused")
                return

        if self.token and scope.get("path", "").startswith("/api/"):
            given = _token_from(headers)
            if not given or not hmac.compare_digest(given.encode("utf-8"), self.token):
                await _deny(
                    scope, send, 401, "Missing or wrong token",
                    extra=[(b"www-authenticate", b"Bearer")],
                )
                return

        await self.app(scope, receive, send)


def _token_from(headers: dict[str, str]) -> str:
    auth = headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return headers.get(TOKEN_HEADER, "").strip()


async def _deny(scope, send, status: int, detail: str, extra=None) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    body = json.dumps({"detail": detail}).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    headers.extend(extra or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
