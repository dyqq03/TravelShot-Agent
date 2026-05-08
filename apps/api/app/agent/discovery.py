from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.agent.llm import complete_json_multimodal, is_llm_configured, is_vision_configured
from app.core.config import settings
from app.spot.cities import CITY_ALIASES, CITY_PROFILES
from app.tools.map import poi_search
from app.tools.search import tavily_search


GENERIC_PLACE_TERMS = {
    "海边",
    "湖边",
    "沙滩",
    "街拍",
    "公园",
    "咖啡馆",
    "森林",
    "城市",
    "古建",
    "沙漠",
    "雪山",
}

SPECIFIC_HINTS = [
    "景山万春亭",
    "万春亭",
    "大小洞天",
    "天涯海角",
    "亚龙湾",
    "蜈支洲岛",
    "西岛",
    "栈桥",
    "琴屿路",
]


def _compact(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum() or "\u4e00" <= ch <= "\u9fff").lower()


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1(":".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _city_from_text(text: str, fallback: str | None = None) -> str | None:
    for city in CITY_PROFILES:
        if city in text:
            return city
    for keyword, city in CITY_ALIASES.items():
        if keyword in text:
            return city
    return fallback


def _spot_summary(spots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "spot_id": item.get("spot_id"),
            "name": item.get("name"),
            "city": item.get("city"),
            "geo_verified": bool(item.get("geo_verified")),
            "location_hint": item.get("location_hint"),
            "visual_elements": item.get("visual_elements") or [],
            "themes": item.get("themes") or [],
            "match_score": item.get("match_score"),
        }
        for item in spots[:12]
    ]


def _is_generic_query(query: str) -> bool:
    compact = _compact(query)
    return compact in {_compact(item) for item in GENERIC_PLACE_TERMS} or len(compact) <= 2


def _find_internal_match(query: str, spots: list[dict[str, Any]]) -> dict[str, Any] | None:
    compact_query = _compact(query)
    if not compact_query:
        return None
    for spot in spots:
        name = _compact(str(spot.get("name") or ""))
        hint = _compact(str(spot.get("location_hint") or ""))
        if compact_query in name or name in compact_query or compact_query in hint:
            return spot
    for spot in spots:
        haystack = _compact(
            " ".join(
                [
                    str(spot.get("name") or ""),
                    str(spot.get("location_hint") or ""),
                    " ".join(spot.get("themes") or []),
                    " ".join(spot.get("visual_elements") or []),
                ]
            )
        )
        if compact_query in haystack:
            return spot
    return None


def _fallback_mentions(user_input: str, parsed_goal: dict[str, Any], internal_spots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    for hint in SPECIFIC_HINTS:
        if hint in user_input:
            query = "景山万春亭" if hint == "万春亭" else hint
            if any(_compact(item["search_query"]) == _compact(query) for item in mentions):
                continue
            mentions.append(
                {
                    "raw_text": hint,
                    "search_query": query,
                    "city": _city_from_text(hint, parsed_goal.get("destination")),
                    "type": "specific_place",
                    "confidence": 0.74,
                }
            )

    if "沙漠" in user_input and not any("沙漠" in " ".join(item.get("visual_elements") or []) for item in internal_spots):
        mentions.append(
            {
                "raw_text": "沙漠",
                "search_query": "中国 沙漠 旅拍 机位 推荐",
                "city": None,
                "type": "generic_scene_without_internal_match",
                "confidence": 0.68,
            }
        )
    return mentions


def analyze_request_for_discovery(
    *,
    user_input: str,
    parsed_goal: dict[str, Any],
    reference_images: list[str],
    internal_spots: list[dict[str, Any]],
    allow_llm: bool = True,
) -> tuple[dict[str, Any], str | None]:
    fallback = {
        "intent_summary": user_input,
        "image_analysis": {},
        "visual_goal_hints": {},
        "location_mentions": _fallback_mentions(user_input, parsed_goal, internal_spots),
        "external_tool_requests": [],
        "search_queries": [],
        "avoid_assumptions": ["外部 LLM 不可用时，只使用规则和工具可核验结果。"],
        "llm_used": False,
    }
    if not allow_llm or not (is_llm_configured() or (reference_images and is_vision_configured())):
        return fallback, None
    if reference_images and not is_vision_configured():
        return fallback, "VISION_API_KEY 未配置，参考图未进入多模态点位/风格分析。"

    payload = {
        "user_input": user_input,
        "parsed_goal": parsed_goal,
        "internal_spot_candidates": _spot_summary(internal_spots),
    }
    result, warning = complete_json_multimodal(
        "你是旅拍助手 Agent 的多模态意图规划器和工具调用决策器。只输出 JSON，不要输出解释。",
        (
            "请先整体理解用户想拍的画面、可能的地点实体、参考图风格，再决定后续工具调用。"
            "你必须遵守：如果用户明确想拍的点位已经在 internal_spot_candidates 中命中且 geo_verified=true，"
            "不要请求 amap_poi_search；如果命中但 geo_verified=false，才请求高德补坐标；如果没有命中，"
            "可以请求 amap_poi_search 或 tavily_search。"
            "模糊长短语要改写成高德更可能搜到的实体，例如“景山万春亭俯拍紫禁城”应搜“景山万春亭”，"
            "“大小洞天海边礁石”应搜“大小洞天”。"
            "如果只是泛泛说“海边”，且库中已有可用海边城市，不要为了海边这个泛词调用高德；"
            "如果是“沙漠”等库里没有的场景，不要编造地点，应请求 tavily_search 或 amap_poi_search 查证。"
            "输出 JSON 字段：intent_summary, image_analysis, visual_goal_hints, location_mentions, external_tool_requests, "
            "search_queries, avoid_assumptions。location_mentions 每项包含 raw_text, search_query, city, type, confidence。"
            "external_tool_requests 每项包含 tool(amap_poi_search/tavily_search), query, city, reason。\n"
            f"输入：{json.dumps(payload, ensure_ascii=False)}"
        ),
        reference_images,
    )
    if not result:
        return fallback, warning

    analysis = dict(fallback)
    for key in ["intent_summary", "image_analysis", "visual_goal_hints", "location_mentions", "external_tool_requests", "search_queries", "avoid_assumptions"]:
        value = result.get(key)
        if value not in (None, "", [], {}):
            analysis[key] = value
    analysis["llm_used"] = True
    return _sanitize_analysis(analysis, parsed_goal, user_input), warning


def _sanitize_analysis(analysis: dict[str, Any], parsed_goal: dict[str, Any], user_input: str) -> dict[str, Any]:
    sanitized = dict(analysis)
    mentions = []
    for item in sanitized.get("location_mentions") or []:
        if not isinstance(item, dict):
            continue
        query = str(item.get("search_query") or item.get("raw_text") or "").strip()
        if not query:
            continue
        mentions.append(
            {
                "raw_text": str(item.get("raw_text") or query),
                "search_query": query,
                "city": item.get("city") or _city_from_text(query, parsed_goal.get("destination")),
                "type": item.get("type") or "specific_place",
                "confidence": _safe_float(item.get("confidence"), 0.6),
            }
        )
    for fallback in _fallback_mentions(user_input, parsed_goal, []):
        if not any(_compact(fallback["search_query"]) == _compact(item["search_query"]) for item in mentions):
            mentions.append(fallback)
    sanitized["location_mentions"] = mentions[:8]

    requests = []
    for item in sanitized.get("external_tool_requests") or []:
        if not isinstance(item, dict):
            continue
        tool = item.get("tool")
        query = str(item.get("query") or "").strip()
        if tool not in {"amap_poi_search", "tavily_search"} or not query:
            continue
        requests.append(
            {
                "tool": tool,
                "query": query,
                "city": item.get("city") or _city_from_text(query, parsed_goal.get("destination")),
                "reason": str(item.get("reason") or "LLM 判断需要外部工具查证。"),
            }
        )
    sanitized["external_tool_requests"] = requests[:6]
    search_queries = [str(item).strip() for item in sanitized.get("search_queries") or [] if str(item).strip()]
    sanitized["search_queries"] = search_queries[:4]
    return sanitized


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _poi_to_spot(poi: dict[str, Any], query: str, parsed_goal: dict[str, Any]) -> dict[str, Any]:
    city = (poi.get("city") or parsed_goal.get("destination") or "").replace("市", "") or "待确认"
    styles = parsed_goal.get("shooting_style") or ["自然旅拍"]
    elements = list(dict.fromkeys([*(parsed_goal.get("visual_elements") or []), *_visual_elements_from_text(f"{query} {poi.get('name')} {poi.get('type')}")]))[:6]
    return {
        "spot_id": _stable_id("amap", str(poi.get("id") or ""), str(poi.get("name") or query), city),
        "name": poi.get("name") or query,
        "city": city,
        "lat": poi.get("lat"),
        "lng": poi.get("lng"),
        "spot_type": _spot_type_from_text(f"{query} {poi.get('type')} {poi.get('name')}"),
        "location_hint": " ".join(str(part) for part in [poi.get("district"), poi.get("address")] if part),
        "source_types": ["map_poi"],
        "source_confidence": 0.68,
        "geo_verified": bool(poi.get("lat") and poi.get("lng")),
        "suitable_styles": styles,
        "visual_elements": elements or ["环境人像"],
        "themes": elements or [query],
        "best_time_hint": ["下午柔光", "黄金时刻"],
        "weather_preference": ["晴天", "多云", "阴天"],
        "ticket_required": False,
        "opening_hours": None,
        "crowd_risk": "medium",
        "phone_friendly": True,
        "base_photo_score": 7.2,
        "shooting_tips": [f"该地点来自高德关键词“{query}”搜索，需要现场核验具体可拍角度。"],
        "recommended_lens_or_focal_length": "手机 1x/2x",
        "access_and_notes": "外部地图 POI，仅代表地点可定位；开放状态、门票和安全边界以现场/官方为准。",
        "source_urls": [],
        "match_score": 7.4,
        "match_reasons": ["高德POI验证", query],
    }


def _visual_elements_from_text(text: str) -> list[str]:
    elements = []
    rules = ["湖边", "海边", "沙滩", "礁石", "街道", "红墙", "古建", "灯塔", "倒影", "桥", "沙漠", "雪山", "森林"]
    for item in rules:
        if item in text or item.replace("边", "") in text:
            elements.append(item)
    return elements


def _spot_type_from_text(text: str) -> str:
    if any(item in text for item in ["海", "沙滩", "礁石", "灯塔"]):
        return "海边"
    if "湖" in text:
        return "湖边"
    if any(item in text for item in ["街", "路"]):
        return "街道"
    if any(item in text for item in ["宫", "寺", "古建", "红墙", "亭"]):
        return "建筑"
    if "沙漠" in text:
        return "自然地貌"
    return "综合机位"


def _merge_spot(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key in ["lat", "lng", "location_hint", "access_and_notes"]:
        if not merged.get(key) and incoming.get(key):
            merged[key] = incoming[key]
    if incoming.get("geo_verified") and not merged.get("geo_verified"):
        merged["lat"] = incoming.get("lat")
        merged["lng"] = incoming.get("lng")
        merged["geo_verified"] = True
    for key in ["source_types", "visual_elements", "themes", "suitable_styles", "match_reasons", "shooting_tips"]:
        merged[key] = list(dict.fromkeys([*(merged.get(key) or []), *(incoming.get(key) or [])]))
    merged["source_confidence"] = round(max(float(merged.get("source_confidence") or 0), float(incoming.get("source_confidence") or 0)), 2)
    merged["match_score"] = round(min(max(float(merged.get("match_score") or 0), float(incoming.get("match_score") or 0)) + 0.2, 10), 2)
    return merged


def fuse_candidate_spots(spots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fused: list[dict[str, Any]] = []
    for spot in spots:
        name_key = _compact(str(spot.get("name") or ""))
        match_index = None
        for index, existing in enumerate(fused):
            existing_key = _compact(str(existing.get("name") or ""))
            if name_key and (name_key == existing_key or name_key in existing_key or existing_key in name_key):
                match_index = index
                break
        if match_index is None:
            fused.append(dict(spot))
        else:
            fused[match_index] = _merge_spot(fused[match_index], spot)
    fused.sort(key=lambda item: (bool(item.get("geo_verified")), item.get("match_score") or 0, item.get("source_confidence") or 0), reverse=True)
    return fused


def execute_discovery_tools(
    *,
    parsed_goal: dict[str, Any],
    internal_spots: list[dict[str, Any]],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    warnings: list[str] = []
    map_searches: list[dict[str, Any]] = []
    reference_searches: list[dict[str, Any]] = []
    external_spots: list[dict[str, Any]] = []
    skipped_map_requests: list[dict[str, Any]] = []

    requests = list(analysis.get("external_tool_requests") or [])
    for mention in analysis.get("location_mentions") or []:
        query = mention.get("search_query")
        if not query:
            continue
        if mention.get("type") == "generic_scene_without_internal_match":
            requests.append({"tool": "tavily_search", "query": query, "city": mention.get("city"), "reason": "机位库没有该场景类型，需要公开搜索查证真实地点。"})
            continue
        match = _find_internal_match(str(query), internal_spots)
        if match and match.get("geo_verified"):
            skipped_map_requests.append({"query": query, "reason": "内置机位库已命中且有经纬度。", "matched_spot": match.get("name")})
            continue
        if match and not match.get("geo_verified"):
            requests.append({"tool": "amap_poi_search", "query": query, "city": match.get("city"), "reason": "内置机位命中但缺少可信经纬度。"})
            continue
        if not _is_generic_query(str(query)):
            requests.append({"tool": "amap_poi_search", "query": query, "city": mention.get("city"), "reason": "用户明确指定的地点不在内置机位库中。"})

    if len(internal_spots) < 3:
        destination = parsed_goal.get("destination")
        scene_terms = " ".join(parsed_goal.get("visual_elements") or parsed_goal.get("shooting_style") or [])
        destination_term = destination if destination and destination != "待推荐" else "中国"
        query = " ".join(part for part in [destination_term, scene_terms, "旅拍 机位 推荐"] if part).strip()
        requests.append({"tool": "tavily_search", "query": query, "city": destination, "reason": "内置候选机位不足，需要公开内容补充线索。"})

    for query in analysis.get("search_queries") or []:
        requests.append({"tool": "tavily_search", "query": query, "city": parsed_goal.get("destination"), "reason": "LLM 生成的参考内容检索关键词。"})

    seen_requests: set[tuple[str, str, str]] = set()
    for request in requests:
        tool = request.get("tool")
        query = str(request.get("query") or "").strip()
        city = request.get("city")
        key = (str(tool), _compact(query), str(city or ""))
        if not query or key in seen_requests:
            continue
        seen_requests.add(key)
        if tool == "amap_poi_search":
            if _is_generic_query(query):
                skipped_map_requests.append({"query": query, "reason": "关键词过泛，避免无效高德搜索。"})
                continue
            match = _find_internal_match(query, internal_spots)
            if match and match.get("geo_verified"):
                skipped_map_requests.append({"query": query, "reason": "执行前再次确认内置库已有可信坐标。", "matched_spot": match.get("name")})
                continue
            result = poi_search(query, city=city)
            pois = (result.get("data") or {}).get("pois") or []
            map_searches.append({"request": request, "result": result})
            if not result.get("success"):
                warnings.append(result.get("error") or f"高德 POI 搜索“{query}”失败。")
            external_spots.extend(_poi_to_spot(poi, query, parsed_goal) for poi in pois[:3])
        elif tool == "tavily_search":
            result = tavily_search(query, max_results=settings.tavily_max_results)
            reference_searches.append({"request": request, "result": result})
            if not result.get("success"):
                warnings.append(result.get("error") or f"Tavily 搜索“{query}”失败。")

    reference_results = []
    for item in reference_searches:
        reference_results.extend(((item.get("result") or {}).get("data") or {}).get("results") or [])

    fused = fuse_candidate_spots([*internal_spots, *external_spots])
    reference_context = {
        "queries": [item["request"].get("query") for item in reference_searches],
        "results": reference_results[:10],
        "shooting_clues": _extract_reference_clues(reference_results, analysis),
    }
    return {
        "candidate_spots": fused,
        "reference_context": reference_context,
        "map_poi_searches": map_searches,
        "reference_searches": reference_searches,
        "skipped_map_requests": skipped_map_requests,
        "warnings": warnings,
    }


def _extract_reference_clues(results: list[dict[str, Any]], analysis: dict[str, Any]) -> list[dict[str, Any]]:
    clues = []
    for item in results[:6]:
        text = f"{item.get('title') or ''} {item.get('summary') or ''}"
        elements = _visual_elements_from_text(text)
        if not elements:
            continue
        clues.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "visual_elements": elements,
                "summary": item.get("summary"),
            }
        )
    image_analysis = analysis.get("image_analysis") if isinstance(analysis.get("image_analysis"), dict) else {}
    if image_analysis:
        clues.insert(
            0,
            {
                "title": "参考图分析",
                "visual_elements": image_analysis.get("visual_elements") or image_analysis.get("scene_elements") or [],
                "summary": image_analysis.get("style_summary") or image_analysis.get("description") or "来自用户上传参考图的风格线索。",
            },
        )
    return clues[:8]
