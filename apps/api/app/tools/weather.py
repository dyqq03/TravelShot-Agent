from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from statistics import mean
from typing import Any

from app.core.config import settings
from app.spot.cities import CITY_PROFILES, get_city_profile
from app.tools.base import tool_result
from app.tools.cache import get_cached_tool_result, set_cached_tool_result


def _fallback_weather(parsed_goal: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
    city = parsed_goal.get("destination") or "杭州"
    target_date = (parsed_goal.get("date_range") or ["待确认"])[0]
    return {
        "status": "fallback",
        "city": city,
        "date": target_date,
        "summary": "未获取到实时天气，使用保守拍摄假设：多云、轻风、降水风险中低。",
        "temperature_range": "18-25",
        "max_precipitation_probability": 35,
        "avg_cloud_cover": 55,
        "max_wind_speed": 18,
        "hourly": [
            {"time": "14:00", "temperature": 24, "precipitation_probability": 25, "cloud_cover": 55, "wind_speed": 14},
            {"time": "16:00", "temperature": 23, "precipitation_probability": 30, "cloud_cover": 58, "wind_speed": 16},
            {"time": "18:00", "temperature": 22, "precipitation_probability": 35, "cloud_cover": 52, "wind_speed": 18},
        ],
        "risk_flags": ["天气未实时确认"],
        "shooting_advice": "出发前再次确认天气；优先安排树荫、街道、屋檐等可调整机位。",
        "error": reason,
    }


def _summarize_weather(city: str, target_date: str, payload: dict[str, Any]) -> dict[str, Any]:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    temperatures = hourly.get("temperature_2m") or []
    precip = hourly.get("precipitation_probability") or []
    cloud = hourly.get("cloud_cover") or []
    wind = hourly.get("wind_speed_10m") or []

    selected = []
    for index, raw_time in enumerate(times):
        if not str(raw_time).startswith(target_date):
            continue
        hour = int(str(raw_time)[11:13])
        if 8 <= hour <= 21:
            selected.append(
                {
                    "time": str(raw_time)[11:16],
                    "temperature": temperatures[index] if index < len(temperatures) else None,
                    "precipitation_probability": precip[index] if index < len(precip) else None,
                    "cloud_cover": cloud[index] if index < len(cloud) else None,
                    "wind_speed": wind[index] if index < len(wind) else None,
                }
            )

    if not selected:
        raise ValueError("Open-Meteo response has no usable hourly forecast.")

    temps = [item["temperature"] for item in selected if item["temperature"] is not None]
    precip_values = [item["precipitation_probability"] for item in selected if item["precipitation_probability"] is not None]
    cloud_values = [item["cloud_cover"] for item in selected if item["cloud_cover"] is not None]
    wind_values = [item["wind_speed"] for item in selected if item["wind_speed"] is not None]
    max_precip = max(precip_values) if precip_values else 0
    avg_cloud = round(mean(cloud_values), 1) if cloud_values else 0
    max_wind = max(wind_values) if wind_values else 0

    risk_flags: list[str] = []
    if max_precip >= 60:
        risk_flags.append("降水概率较高")
    if avg_cloud >= 75:
        risk_flags.append("云量偏高，夕阳不稳定")
    if max_wind >= 28:
        risk_flags.append("风力偏大")

    if max_precip >= 60:
        advice = "准备雨天电影感路线，优先屋檐、街道反光和可停留机位。"
    elif avg_cloud >= 70:
        advice = "阴天适合低对比清新人像，夕阳作为可遇不可求的加分项。"
    elif avg_cloud <= 35:
        advice = "晴天适合蓝天和逆光，但 11:00-14:30 避免硬光直拍。"
    else:
        advice = "多云柔光较友好，傍晚如果云层打开可以保留夕阳窗口。"

    return {
        "status": "live",
        "city": city,
        "date": target_date,
        "summary": f"{city}{target_date} 白天云量约 {avg_cloud}%，最高降水概率 {max_precip}%，最大风速 {max_wind} km/h。",
        "temperature_range": f"{min(temps):.0f}-{max(temps):.0f}" if temps else "待确认",
        "max_precipitation_probability": max_precip,
        "avg_cloud_cover": avg_cloud,
        "max_wind_speed": max_wind,
        "hourly": selected[::2],
        "risk_flags": risk_flags,
        "shooting_advice": advice,
        "error": None,
    }


def _float_coord(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _weather_coordinates(parsed_goal: dict[str, Any]) -> tuple[float, float, str] | None:
    lat = _float_coord(parsed_goal.get("lat") or parsed_goal.get("latitude"))
    lng = _float_coord(parsed_goal.get("lng") or parsed_goal.get("longitude"))
    if lat is not None and lng is not None:
        return lat, lng, str(parsed_goal.get("coordinate_source") or "request_coordinates")

    city = parsed_goal.get("destination") or "杭州"
    if city in CITY_PROFILES:
        profile = get_city_profile(city)
        return float(profile["lat"]), float(profile["lng"]), "city_profile"
    return None


def fetch_weather_context(parsed_goal: dict[str, Any]) -> dict[str, Any]:
    city = parsed_goal.get("destination") or "杭州"
    target_date = (parsed_goal.get("date_range") or [None])[0]
    if not target_date:
        return _fallback_weather(parsed_goal, "缺少拍摄日期。")
    coords = _weather_coordinates(parsed_goal)
    if coords is None:
        return _fallback_weather(parsed_goal, f"缺少{city}的经纬度，无法调用 Open-Meteo。")
    lat, lng, coordinate_source = coords

    query = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lng,
            "hourly": "temperature_2m,precipitation_probability,cloud_cover,wind_speed_10m",
            "timezone": "Asia/Shanghai",
            "start_date": target_date,
            "end_date": target_date,
        }
    )
    url = f"{settings.open_meteo_base_url.rstrip('/')}/v1/forecast?{query}"
    cache_payload = {"city": city, "target_date": target_date, "lat": round(lat, 5), "lng": round(lng, 5), "url": url}
    cached = get_cached_tool_result("open_meteo.weather", cache_payload)
    if cached:
        weather = dict((cached.get("data") or {}).get("weather_context") or {})
        weather["cached"] = True
        return weather

    try:
        with urllib.request.urlopen(url, timeout=settings.weather_timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        weather = _summarize_weather(city, target_date, payload)
        weather["observer"] = {"lat": lat, "lng": lng}
        weather["coordinate_source"] = coordinate_source
        set_cached_tool_result(
            "open_meteo.weather",
            cache_payload,
            tool_result(success=True, source="open_meteo.weather", data={"weather_context": weather}),
        )
        return weather
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return _fallback_weather(parsed_goal, str(exc))
