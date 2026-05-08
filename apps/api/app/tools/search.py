from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from app.core.config import settings
from app.tools.base import ToolResult, tool_result
from app.tools.cache import get_cached_tool_result, set_cached_tool_result


def is_search_configured() -> bool:
    return bool(settings.tavily_api_key and settings.tavily_api_key.strip() and settings.tavily_api_key != "your_key_here")


def build_reference_query(parsed_goal: dict[str, Any]) -> str:
    city = parsed_goal.get("destination") or "杭州"
    styles = " ".join((parsed_goal.get("shooting_style") or [])[:3])
    elements = " ".join((parsed_goal.get("visual_elements") or [])[:4])
    return " ".join(part for part in [city, "旅拍 机位 攻略", styles, elements] if part).strip()


def tavily_search(query: str, max_results: int | None = None) -> ToolResult:
    limit = max_results or settings.tavily_max_results
    payload = {
        "query": query,
        "search_depth": settings.tavily_search_depth,
        "max_results": min(max(limit, 1), 20),
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "include_favicon": True,
    }
    cached = get_cached_tool_result("tavily.search", payload)
    if cached:
        return cached
    if not is_search_configured():
        return tool_result(
            success=False,
            source="tavily.search",
            error="TAVILY_API_KEY/SEARCH_API_KEY 未配置，跳过参考内容搜索。",
            data={"query": query, "results": []},
        )

    request = urllib.request.Request(
        f"{settings.tavily_base_url.rstrip('/')}/search",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.tavily_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.tavily_timeout_seconds) as response:
            raw = json.loads(response.read().decode("utf-8"))
        results = [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "summary": item.get("content"),
                "score": item.get("score"),
                "source": "tavily",
            }
            for item in (raw.get("results") or [])[:limit]
        ]
        return set_cached_tool_result(
            "tavily.search",
            payload,
            tool_result(
                success=True,
                source="tavily.search",
                data={
                    "query": raw.get("query") or query,
                    "results": results,
                    "answer": raw.get("answer"),
                    "response_time": raw.get("response_time"),
                    "request_id": raw.get("request_id"),
                },
            ),
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return tool_result(success=False, source="tavily.search", error=str(exc), data={"query": query, "results": []})


def search_reference_content(parsed_goal: dict[str, Any]) -> ToolResult:
    return tavily_search(build_reference_query(parsed_goal), max_results=settings.tavily_max_results)
