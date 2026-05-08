from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.spot.cities import CITY_PROFILES, get_city_profile


def _stable_id(city: str, name: str) -> str:
    digest = hashlib.sha1(f"{city}:{name}".encode("utf-8")).hexdigest()[:12]
    return f"spot_{digest}"


def _candidate_data_dirs() -> list[Path]:
    configured = Path(settings.spot_data_dir)
    parents = list(Path(__file__).resolve().parents)
    root = parents[4] if len(parents) > 4 else parents[min(2, len(parents) - 1)]
    return [
        configured,
        root / settings.spot_data_dir,
        root / "db" / "seed" / "spots",
        Path.cwd() / "db" / "seed" / "spots",
    ]


def _normalize_spot(raw: dict[str, Any]) -> dict[str, Any]:
    city = raw.get("city") or "杭州"
    name = raw.get("name") or "未命名机位"
    themes = raw.get("themes") or []
    location_hint = raw.get("location_hint") or ""
    profile = get_city_profile(city)
    lat = raw.get("lat") or profile["lat"]
    lng = raw.get("lng") or profile["lng"]
    geo_verified = bool(raw.get("geo_verified") or (raw.get("lat") and raw.get("lng"))) and _geo_is_plausible(city, lat, lng)
    return {
        "spot_id": _stable_id(city, name),
        "name": name,
        "city": city,
        "lat": lat,
        "lng": lng,
        "spot_type": _infer_spot_type(raw),
        "location_hint": location_hint,
        "source_types": ["internal_db"],
        "source_confidence": 0.92 if raw.get("confidence") == "high" else 0.78,
        "geo_verified": geo_verified,
        "suitable_styles": _infer_styles(raw),
        "visual_elements": _infer_visual_elements(raw),
        "themes": themes,
        "best_time_hint": [raw.get("best_time")] if raw.get("best_time") else [],
        "weather_preference": _infer_weather_preference(raw),
        "ticket_required": "需购票" in (raw.get("access_and_notes") or ""),
        "opening_hours": None,
        "crowd_risk": "high" if "人多" in (raw.get("access_and_notes") or "") or "热门" in name else "medium",
        "phone_friendly": "长焦" not in (raw.get("recommended_lens_or_focal_length") or ""),
        "base_photo_score": 8.6 if raw.get("confidence") == "high" else 7.8,
        "shooting_tips": [raw.get("shooting_tips")] if raw.get("shooting_tips") else [],
        "recommended_lens_or_focal_length": raw.get("recommended_lens_or_focal_length") or "手机 1x/2x",
        "access_and_notes": raw.get("access_and_notes") or "",
        "source_urls": raw.get("source_urls") or [],
    }


def _infer_spot_type(raw: dict[str, Any]) -> str:
    haystack = f"{raw.get('name', '')} {' '.join(raw.get('themes') or [])} {raw.get('location_hint', '')}"
    if any(item in haystack for item in ["湖", "西湖", "玄武湖"]):
        return "湖边"
    if any(item in haystack for item in ["海", "沙滩", "灯塔", "栈桥"]):
        return "海边"
    if any(item in haystack for item in ["街", "路", "骑楼", "公路"]):
        return "街道"
    if any(item in haystack for item in ["古建", "红墙", "寺", "殿", "城墙"]):
        return "建筑"
    if any(item in haystack for item in ["公园", "植物", "绿"]):
        return "公园"
    return "综合机位"


def _infer_styles(raw: dict[str, Any]) -> list[str]:
    haystack = f"{raw.get('name', '')} {' '.join(raw.get('themes') or [])} {raw.get('shooting_tips', '')}"
    styles = []
    rules = {
        "日系清新": ["日系", "绿意", "湖边", "海边", "街拍", "生活"],
        "电影感": ["电影", "蓝调", "夜景", "雨", "街", "倒影"],
        "国风": ["古建", "红墙", "寺", "城墙", "国风"],
        "海边": ["海", "沙滩", "灯塔", "栈桥"],
        "湖边": ["湖", "水面"],
        "城市漫游": ["街", "路", "建筑", "骑楼"],
        "自然": ["公园", "树", "花", "绿", "海", "湖"],
        "夕阳": ["日落", "夕阳", "傍晚"],
        "蓝调": ["蓝调", "夜景"],
    }
    for style, keywords in rules.items():
        if any(keyword in haystack for keyword in keywords):
            styles.append(style)
    return styles or ["自然旅拍"]


def _infer_visual_elements(raw: dict[str, Any]) -> list[str]:
    haystack = f"{raw.get('name', '')} {' '.join(raw.get('themes') or [])} {raw.get('shooting_tips', '')}"
    elements = []
    for item in ["湖边", "海边", "树荫", "夕阳", "蓝天", "沙滩", "街道", "红墙", "古建", "灯塔", "倒影", "桥", "长椅", "花", "梧桐"]:
        key = item.replace("边", "")
        if item in haystack or key in haystack:
            elements.append(item)
    return elements or (raw.get("themes") or [])[:4]


def _infer_weather_preference(raw: dict[str, Any]) -> list[str]:
    haystack = f"{raw.get('best_time', '')} {' '.join(raw.get('themes') or [])}"
    if "雨" in haystack:
        return ["小雨", "阴天", "多云"]
    if "蓝调" in haystack or "夜景" in haystack:
        return ["晴天", "多云"]
    if "日出" in haystack or "日落" in haystack:
        return ["晴天", "少云"]
    return ["晴天", "多云", "阴天"]


@lru_cache(maxsize=4)
def load_spots() -> tuple[dict[str, Any], ...]:
    data_dir = next((path for path in _candidate_data_dirs() if path.exists()), None)
    if data_dir is None:
        return tuple()

    spots: list[dict[str, Any]] = []
    for path in sorted(data_dir.glob("spots_*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    spots.append(_normalize_spot(json.loads(line)))
                except json.JSONDecodeError:
                    continue
    return tuple(spots)


def search_candidate_spots(parsed_goal: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    city = parsed_goal.get("destination") or "杭州"
    styles = set(parsed_goal.get("shooting_style") or [])
    elements = set(parsed_goal.get("visual_elements") or [])
    required_external_scenes = elements.intersection({"沙漠", "雪山", "草原"})
    if city and city != "待推荐":
        city_spots = [spot for spot in load_spots() if spot["city"] == city]
    else:
        city_spots = list(load_spots())

    scored: list[dict[str, Any]] = []
    for spot in city_spots:
        style_matches = styles.intersection(spot.get("suitable_styles") or [])
        element_matches = elements.intersection(spot.get("visual_elements") or [])
        theme_text = " ".join(spot.get("themes") or [])
        raw_text = f"{spot['name']} {spot['location_hint']} {theme_text} {' '.join(spot.get('shooting_tips') or [])}"
        fuzzy_matches = sum(1 for item in styles.union(elements) if item and item in raw_text)
        if city not in CITY_PROFILES and required_external_scenes and not any(scene in raw_text for scene in required_external_scenes):
            continue
        match_score = (
            spot.get("base_photo_score", 7.5)
            + len(style_matches) * 0.8
            + len(element_matches) * 0.9
            + fuzzy_matches * 0.35
            + spot.get("source_confidence", 0.7)
        )
        exact_user_match = False
        if _compact(spot["name"]) and _compact(spot["name"]) in _compact(parsed_goal.get("raw_text") or ""):
            match_score += 3.0
            exact_user_match = True
        elif any(_compact(token) in _compact(parsed_goal.get("raw_text") or "") for token in _important_name_tokens(spot["name"])):
            match_score += 1.2
            exact_user_match = True
        candidate = dict(spot)
        candidate["match_score"] = round(min(match_score, 10.0), 2)
        candidate["exact_user_match"] = exact_user_match
        candidate["match_reasons"] = list(style_matches) + list(element_matches)
        if not candidate["match_reasons"]:
            candidate["match_reasons"] = candidate.get("themes", [])[:2]
        scored.append(candidate)

    scored.sort(key=lambda item: (bool(item.get("exact_user_match")), item["match_score"]), reverse=True)
    return scored[:limit]


def _compact(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum() or "\u4e00" <= ch <= "\u9fff").lower()


def _important_name_tokens(name: str) -> list[str]:
    compact = _compact(name)
    tokens = []
    for size in range(5, 1, -1):
        tokens.extend(compact[index:index + size] for index in range(max(len(compact) - size + 1, 0)))
    return [token for token in tokens if len(token) >= 2][:18]


def _distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    earth_radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * earth_radius_km * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _geo_is_plausible(city: str, lat: object, lng: object) -> bool:
    profile = CITY_PROFILES.get(city)
    if not profile or lat is None or lng is None:
        return bool(lat is not None and lng is not None)
    try:
        return _distance_km(float(lat), float(lng), float(profile["lat"]), float(profile["lng"])) <= 140
    except (TypeError, ValueError):
        return False
