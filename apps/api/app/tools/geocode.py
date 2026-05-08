from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.core.config import settings
from app.tools.base import ToolResult, tool_result
from app.tools.cache import get_cached_tool_result, set_cached_tool_result


def nominatim_geocode(query: str, city: str | None = None, limit: int = 3) -> ToolResult:
    normalized_query = " ".join(str(query or "").split()).strip()
    normalized_city = str(city or "").replace("市", "").strip()
    if not normalized_query:
        return tool_result(
            success=False,
            source="nominatim.geocode",
            error="Nominatim 查询关键词为空。",
            data={"query": query, "results": []},
        )

    search_text = normalized_query
    if normalized_city and normalized_city not in normalized_query:
        search_text = f"{normalized_query} {normalized_city} 中国"
    payload = {"query": search_text, "limit": min(max(limit, 1), 10)}
    cached = get_cached_tool_result("nominatim.geocode", payload)
    if cached:
        return cached

    params = {
        "format": "jsonv2",
        "q": search_text,
        "limit": payload["limit"],
        "addressdetails": 1,
        "countrycodes": "cn",
    }
    if settings.nominatim_email:
        params["email"] = settings.nominatim_email
    url = f"{settings.nominatim_base_url.rstrip('/')}/search?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "TravelShotAgent/0.1",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.4",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.nominatim_timeout_seconds) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return tool_result(
            success=False,
            source="nominatim.geocode",
            error=f"Nominatim 调用失败：{exc}",
            data={"query": search_text, "results": []},
        )

    results: list[dict[str, Any]] = []
    for item in raw[: payload["limit"]]:
        try:
            lat = float(item.get("lat"))
            lng = float(item.get("lon"))
        except (TypeError, ValueError):
            continue
        results.append(
            {
                "name": item.get("name") or normalized_query,
                "display_name": item.get("display_name"),
                "lat": lat,
                "lng": lng,
                "importance": item.get("importance"),
                "osm_type": item.get("osm_type"),
                "osm_id": item.get("osm_id"),
                "source": "nominatim",
            }
        )

    return set_cached_tool_result(
        "nominatim.geocode",
        payload,
        tool_result(
            success=bool(results),
            source="nominatim.geocode",
            error=None if results else "Nominatim 未返回可用经纬度。",
            data={"query": search_text, "results": results, "count": len(results)},
        ),
    )
