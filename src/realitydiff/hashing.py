from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha1_hex(payload: str | bytes) -> str:
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hashlib.sha1(data).hexdigest()


def object_id(kind: str, value: Any) -> str:
    blob = f"{kind} {canonical_json(value)}"
    return sha1_hex(blob)


def stable_item_id(prefix: str, *parts: str) -> str:
    material = "\n".join(part.strip() for part in parts if part)
    return f"{prefix}_{sha1_hex(material)[:12]}"
