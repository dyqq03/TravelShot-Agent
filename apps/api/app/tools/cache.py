from __future__ import annotations

import hashlib
import json
import socket
import time
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings
from app.tools.base import ToolResult


_CACHE: dict[str, tuple[float, ToolResult]] = {}


def _stable_key(namespace: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"


def get_cached_tool_result(namespace: str, payload: dict[str, Any]) -> ToolResult | None:
    key = _stable_key(namespace, payload)
    item = _CACHE.get(key)
    if item:
        expires_at, result = item
        if expires_at < time.time():
            _CACHE.pop(key, None)
            return None
        return _mark_cached(result)

    result = _redis_get(key)
    if result:
        return _mark_cached(result)
    return None


def _mark_cached(result: ToolResult) -> ToolResult:
    cached = dict(result)
    data = dict(cached.get("data") or {})
    data["cached"] = True
    cached["data"] = data
    return cached  # type: ignore[return-value]


def set_cached_tool_result(namespace: str, payload: dict[str, Any], result: ToolResult) -> ToolResult:
    ttl = max(settings.tool_cache_ttl_seconds, 0)
    if ttl:
        key = _stable_key(namespace, payload)
        _CACHE[key] = (time.time() + ttl, result)
        _redis_set(key, result, ttl)
    return result


def _redis_command(parts: list[str], timeout: float = 0.4) -> bytes | None:
    parsed = urlparse(settings.redis_url)
    if not parsed.hostname:
        return None
    host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
    port = parsed.port or 6379
    db = (parsed.path or "/0").lstrip("/") or "0"
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            if parsed.password:
                auth_parts = ["AUTH"]
                if parsed.username:
                    auth_parts.append(parsed.username)
                auth_parts.append(parsed.password)
                sock.sendall(_encode_resp(auth_parts))
                _ = sock.recv(512)
            if db != "0":
                sock.sendall(_encode_resp(["SELECT", db]))
                _ = sock.recv(512)
            sock.sendall(_encode_resp(parts))
            return sock.recv(1024 * 1024)
    except OSError:
        return None


def _encode_resp(parts: list[str]) -> bytes:
    payload = bytearray(f"*{len(parts)}\r\n".encode("utf-8"))
    for part in parts:
        encoded = str(part).encode("utf-8")
        payload.extend(f"${len(encoded)}\r\n".encode("utf-8"))
        payload.extend(encoded)
        payload.extend(b"\r\n")
    return bytes(payload)


def _redis_bulk_value(response: bytes | None) -> str | None:
    if not response or response.startswith(b"$-1") or not response.startswith(b"$"):
        return None
    try:
        _, rest = response.split(b"\r\n", 1)
        value, _ = rest.split(b"\r\n", 1)
        return value.decode("utf-8")
    except ValueError:
        return None


def _redis_get(key: str) -> ToolResult | None:
    raw = _redis_bulk_value(_redis_command(["GET", key]))
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def _redis_set(key: str, result: ToolResult, ttl: int) -> None:
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
    _redis_command(["SETEX", key, str(ttl), payload])
