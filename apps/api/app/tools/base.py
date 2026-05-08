from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TypedDict


class ToolResult(TypedDict):
    success: bool
    data: dict[str, Any]
    error: str | None
    source: str
    fetched_at: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def tool_result(
    *,
    success: bool,
    data: dict[str, Any] | None = None,
    error: str | None = None,
    source: str,
) -> ToolResult:
    return {
        "success": success,
        "data": data or {},
        "error": error,
        "source": source,
        "fetched_at": now_iso(),
    }
