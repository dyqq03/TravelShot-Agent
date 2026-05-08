from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.core.config import settings
from app.tools.base import ToolResult, tool_result
from app.tools.cache import get_cached_tool_result, set_cached_tool_result


TRAVEL_MODES = ("walking", "bicycling", "taxi", "transit")
MAX_LOCAL_TRANSFER_DISTANCE_M = 120_000

MODE_LABELS = {
    "walking": "步行",
    "bicycling": "骑行",
    "taxi": "打车",
    "transit": "公交/地铁",
}


def is_map_configured() -> bool:
    return bool(settings.amap_api_key and settings.amap_api_key.strip() and settings.amap_api_key != "your_key_here")


def _coords(point: dict[str, Any]) -> tuple[float, float] | None:
    if point.get("geo_verified") is False:
        return None
    lat = point.get("lat") or point.get("latitude")
    lng = point.get("lng") or point.get("longitude")
    if lat is None or lng is None:
        return None
    try:
        return float(lat), float(lng)
    except (TypeError, ValueError):
        return None


def _amap_location(lat: float, lng: float) -> str:
    return f"{lng:.6f},{lat:.6f}"


def _http_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    url = f"{settings.amap_base_url.rstrip('/')}{path}?{query}"
    with urllib.request.urlopen(url, timeout=settings.amap_timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _haversine_distance_m(origin: tuple[float, float], destination: tuple[float, float]) -> float:
    lat1, lng1 = origin
    lat2, lng2 = destination
    earth_radius_m = 6_371_000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * earth_radius_m * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _mode_estimate(distance_m: float, mode: str) -> tuple[int, int]:
    if mode == "walking":
        road_distance = distance_m * 1.35
        duration = int(road_distance / 1.15)
    elif mode == "bicycling":
        road_distance = distance_m * 1.25
        duration = int(road_distance / 3.8)
    elif mode == "taxi":
        road_distance = distance_m * 1.22
        duration = int(road_distance / 7.0) + 5 * 60
    else:
        road_distance = distance_m * 1.35
        duration = int(road_distance / 5.5) + 8 * 60
    return int(road_distance), duration


def _summary(mode: str, duration_seconds: int | None, distance_m: int | None, source: str, cost: str | None = None) -> str:
    label = MODE_LABELS.get(mode, mode)
    if duration_seconds is None:
        return f"{label}耗时暂不可计算。"
    minutes = round(duration_seconds / 60)
    distance_text = f"，距离约 {round(distance_m / 1000, 1)} 公里" if distance_m else ""
    cost_text = f"，费用约 {cost} 元" if cost else ""
    prefix = "高德" if source.startswith("amap") else "估算"
    return f"{prefix}{label}约 {minutes} 分钟{distance_text}{cost_text}。"


def _estimated_route_time(origin: dict[str, Any], destination: dict[str, Any], mode: str, reason: str) -> ToolResult:
    origin_coords = _coords(origin)
    destination_coords = _coords(destination)
    label = MODE_LABELS.get(mode, mode)
    if not origin_coords or not destination_coords:
        return tool_result(
            success=False,
            source=f"route_time.estimate.{mode}",
            error=reason,
            data={
                "mode": mode,
                "mode_label": label,
                "distance_m": None,
                "duration_seconds": None,
                "duration_minutes": None,
                "summary": "机位缺少精确经纬度，建议同片区移动按 15-25 分钟缓冲预留。",
            },
        )

    distance = _haversine_distance_m(origin_coords, destination_coords)
    road_distance, duration = _mode_estimate(distance, mode)
    return tool_result(
        success=False,
        source=f"route_time.estimate.{mode}",
        error=reason,
        data={
            "mode": mode,
            "mode_label": label,
            "distance_m": road_distance,
            "duration_seconds": duration,
            "duration_minutes": round(duration / 60),
            "summary": _summary(mode, duration, road_distance, "estimate"),
        },
    )


def _parse_v3_path(raw: dict[str, Any]) -> tuple[int | None, int | None]:
    paths = ((raw.get("route") or {}).get("paths") or [])
    first = paths[0] if paths else {}
    try:
        distance = int(float(first.get("distance") or 0)) or None
        duration = int(float(first.get("duration") or 0)) or None
    except (TypeError, ValueError):
        return None, None
    return distance, duration


def _parse_bicycling_path(raw: dict[str, Any]) -> tuple[int | None, int | None]:
    paths = ((raw.get("data") or {}).get("paths") or [])
    first = paths[0] if paths else {}
    try:
        distance = int(float(first.get("distance") or 0)) or None
        duration = int(float(first.get("duration") or 0)) or None
    except (TypeError, ValueError):
        return None, None
    return distance, duration


def _parse_transit_path(raw: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    transits = ((raw.get("route") or {}).get("transits") or [])
    if not transits:
        return None, None, None
    first = min(transits, key=lambda item: int(float(item.get("duration") or 999999)))
    try:
        duration = int(float(first.get("duration") or 0)) or None
        distance = int(float(first.get("distance") or 0)) or None
    except (TypeError, ValueError):
        duration = None
        distance = None
    cost = first.get("cost")
    return distance, duration, str(cost) if cost not in (None, "") else None


def _route_path_and_params(origin: dict[str, Any], destination: dict[str, Any], mode: str) -> tuple[str, dict[str, Any]]:
    origin_coords = _coords(origin)
    destination_coords = _coords(destination)
    if origin_coords is None or destination_coords is None:
        raise ValueError("缺少经纬度，无法调用高德路线。")
    lat1, lng1 = origin_coords
    lat2, lng2 = destination_coords
    params = {
        "key": settings.amap_api_key,
        "origin": _amap_location(lat1, lng1),
        "destination": _amap_location(lat2, lng2),
        "output": "JSON",
    }
    if mode == "walking":
        return "/v3/direction/walking", params
    if mode == "bicycling":
        return "/v4/direction/bicycling", params
    if mode == "taxi":
        params["extensions"] = "base"
        return "/v3/direction/driving", params
    if mode == "transit":
        city = origin.get("city") or destination.get("city")
        cityd = destination.get("city") or city
        if city:
            params["city"] = city
        if cityd and cityd != city:
            params["cityd"] = cityd
        params["strategy"] = "0"
        return "/v3/direction/transit/integrated", params
    raise ValueError(f"Unsupported route mode: {mode}")


def _route_time_from_amap(origin: dict[str, Any], destination: dict[str, Any], mode: str) -> ToolResult:
    path, params = _route_path_and_params(origin, destination, mode)
    raw = _http_get(path, params)
    cost = None
    if mode == "bicycling":
        ok = raw.get("errcode") in (0, "0") or str(raw.get("status")) == "1"
        distance, duration = _parse_bicycling_path(raw)
    elif mode == "transit":
        ok = str(raw.get("status")) == "1"
        distance, duration, cost = _parse_transit_path(raw)
    else:
        ok = str(raw.get("status")) == "1"
        distance, duration = _parse_v3_path(raw)
        if mode == "taxi":
            route_cost = (raw.get("route") or {}).get("taxi_cost")
            cost = str(route_cost) if route_cost not in (None, "") else None

    if not ok or duration is None:
        message = raw.get("info") or raw.get("errmsg") or raw.get("infocode") or "高德路线查询失败。"
        return _estimated_route_time(origin, destination, mode, str(message))

    source = f"amap.route_time.{mode}"
    return tool_result(
        success=True,
        source=source,
        data={
            "mode": mode,
            "mode_label": MODE_LABELS.get(mode, mode),
            "distance_m": distance,
            "duration_seconds": duration,
            "duration_minutes": round(duration / 60),
            "cost": cost,
            "summary": _summary(mode, duration, distance, source, cost=cost),
        },
    )


def route_time(origin: dict[str, Any], destination: dict[str, Any], mode: str = "walking") -> ToolResult:
    if mode not in TRAVEL_MODES:
        raise ValueError(f"Unsupported route mode: {mode}")
    payload = {
        "origin": {
            "name": origin.get("spot_name") or origin.get("name"),
            "lat": origin.get("lat"),
            "lng": origin.get("lng"),
            "geo_verified": origin.get("geo_verified"),
        },
        "destination": {
            "name": destination.get("spot_name") or destination.get("name"),
            "lat": destination.get("lat"),
            "lng": destination.get("lng"),
            "geo_verified": destination.get("geo_verified"),
        },
        "mode": mode,
    }
    cached = get_cached_tool_result("amap.route_time", payload)
    if cached:
        return cached
    if not _coords(origin) or not _coords(destination):
        return _estimated_route_time(origin, destination, mode, "缺少经纬度，无法调用高德路线。")
    if not is_map_configured():
        return _estimated_route_time(origin, destination, mode, "AMAP_API_KEY/MAPS_API_KEY 未配置，使用路线耗时估算。")

    try:
        result = _route_time_from_amap(origin, destination, mode)
        return set_cached_tool_result("amap.route_time", payload, result)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return _estimated_route_time(origin, destination, mode, str(exc))


def _option_score(option: dict[str, Any]) -> float:
    duration = option.get("duration_minutes")
    distance = option.get("distance_m") or 0
    if duration is None:
        return -999.0
    score = 100 - min(float(duration), 90)
    mode = option.get("mode")
    if mode == "walking":
        if distance <= 1_500:
            score += 9
        elif distance > 2_500:
            score -= 28
    elif mode == "bicycling":
        if 800 <= distance <= 5_000:
            score += 7
        elif distance > 8_000:
            score -= 18
    elif mode == "taxi":
        score -= 8
        if distance >= 3_000:
            score += 13
        if duration <= 15:
            score += 5
    elif mode == "transit":
        score -= 14
        if distance < 3_000:
            score -= 15
        elif distance >= 6_000:
            score += 6
    if not option.get("success"):
        score -= 8
    return round(score, 2)


def _recommendation_reason(option: dict[str, Any]) -> str:
    mode = option.get("mode")
    distance = option.get("distance_m") or 0
    if mode == "walking":
        return "距离较近，步行换点最稳定。"
    if mode == "bicycling":
        return "距离适中，骑行速度和灵活性较好。"
    if mode == "taxi":
        return "跨片区或赶光线时更稳，等同于打车方案。"
    if mode == "transit":
        return "适合预算敏感或距离较远但不赶时间的情况。"
    if distance:
        return "根据距离和耗时综合排序。"
    return "等待补充坐标后可重新计算。"


def route_options(origin: dict[str, Any], destination: dict[str, Any], modes: tuple[str, ...] = TRAVEL_MODES) -> ToolResult:
    origin_coords = _coords(origin)
    destination_coords = _coords(destination)
    if origin_coords and destination_coords:
        direct_distance = _haversine_distance_m(origin_coords, destination_coords)
        if direct_distance > MAX_LOCAL_TRANSFER_DISTANCE_M:
            return tool_result(
                success=False,
                source="amap.route_options.skipped_long_distance",
                error="两点直线距离超过本地换点阈值，跳过步行/打车等城市内路线工具。",
                data={
                    "from": origin.get("spot_name") or origin.get("name"),
                    "to": destination.get("spot_name") or destination.get("name"),
                    "direct_distance_m": round(direct_distance),
                    "recommended": None,
                    "options": [],
                    "summary": "跨城市/跨区域移动，请按城际交通或自驾段单独规划。",
                },
            )
    results = [route_time(origin, destination, mode) for mode in modes]
    options = []
    for result in results:
        data = dict(result.get("data") or {})
        data["success"] = bool(result.get("success"))
        data["source"] = result.get("source")
        data["error"] = result.get("error")
        data["score"] = _option_score(data)
        data["recommendation_reason"] = _recommendation_reason(data)
        options.append(data)

    ranked = sorted(options, key=lambda item: item.get("score", -999), reverse=True)
    usable = [item for item in ranked if item.get("duration_minutes") is not None]
    recommended = usable[0] if usable else ranked[0] if ranked else None
    return tool_result(
        success=bool(usable),
        source="amap.route_options",
        error=None if usable else "缺少可用经纬度或路线工具未返回可用耗时。",
        data={
            "from": origin.get("spot_name") or origin.get("name"),
            "to": destination.get("spot_name") or destination.get("name"),
            "recommended": recommended,
            "options": ranked[:4],
        },
    )


def _parse_poi_location(value: str | None) -> tuple[float | None, float | None]:
    if not value or "," not in value:
        return None, None
    lng_text, lat_text = value.split(",", 1)
    try:
        return float(lat_text), float(lng_text)
    except ValueError:
        return None, None


def poi_search(query: str, city: str | None = None, limit: int = 5) -> ToolResult:
    normalized_query = query.strip()
    payload = {"query": normalized_query, "city": city or "", "limit": min(max(limit, 1), 20)}
    cached = get_cached_tool_result("amap.poi_search", payload)
    if cached:
        return cached
    if not normalized_query:
        return tool_result(success=False, source="amap.poi_search", error="POI 搜索关键词为空。", data={"query": query, "pois": []})
    if not is_map_configured():
        return tool_result(
            success=False,
            source="amap.poi_search",
            error="AMAP_API_KEY/MAPS_API_KEY 未配置，跳过高德关键词搜索。",
            data={"query": normalized_query, "city": city, "pois": []},
        )

    params: dict[str, Any] = {
        "key": settings.amap_api_key,
        "keywords": normalized_query,
        "offset": payload["limit"],
        "page": 1,
        "extensions": "base",
        "output": "JSON",
    }
    if city:
        params["city"] = city
        params["citylimit"] = "true"

    try:
        raw = _http_get("/v3/place/text", params)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return tool_result(success=False, source="amap.poi_search", error=str(exc), data={"query": normalized_query, "city": city, "pois": []})

    if str(raw.get("status")) != "1":
        return tool_result(
            success=False,
            source="amap.poi_search",
            error=raw.get("info") or raw.get("infocode") or "高德 POI 搜索失败。",
            data={"query": normalized_query, "city": city, "pois": []},
        )

    pois = []
    for item in (raw.get("pois") or [])[:payload["limit"]]:
        lat, lng = _parse_poi_location(item.get("location"))
        pois.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "type": item.get("type"),
                "address": item.get("address") if isinstance(item.get("address"), str) else "",
                "city": item.get("cityname") or city,
                "district": item.get("adname"),
                "lat": lat,
                "lng": lng,
                "source": "amap.poi_search",
            }
        )
    return set_cached_tool_result(
        "amap.poi_search",
        payload,
        tool_result(
            success=bool(pois),
            source="amap.poi_search",
            error=None if pois else "高德 POI 未返回候选地点。",
            data={"query": normalized_query, "city": city, "pois": pois, "count": len(pois)},
        ),
    )
