from __future__ import annotations

import re
from typing import Any

from app.agent.llm import complete_json_multimodal, is_llm_configured
from app.agent.state import AgentState
from app.core.config import settings
from app.spot.cities import COASTAL_DEFAULT_CITY, has_destination_signal, infer_city, infer_departure_city, parse_date_range


STYLE_KEYWORDS = [
    "写真",
    "日系清新",
    "电影感",
    "海边",
    "湖边",
    "自然",
    "夕阳",
    "日出",
    "日落",
    "蓝调",
    "夜景",
    "国风",
    "古风",
    "城市漫游",
    "街拍",
    "森系",
    "胶片感",
]

VISUAL_KEYWORDS = [
    "白裙",
    "湖边",
    "海边",
    "树荫",
    "夕阳",
    "日出",
    "日落",
    "蓝天",
    "沙滩",
    "长椅",
    "街道",
    "红墙",
    "古建",
    "礁石",
    "沙漠",
    "雪山",
    "灯塔",
    "倒影",
    "花",
    "梧桐",
    "桥",
    "长城",
    "城墙",
    "烽火台",
    "山脊",
    "咖啡",
]

PLATFORM_KEYWORDS = ["小红书", "朋友圈", "抖音", "微博", "Instagram"]


def _find_equipment(text: str) -> list[str]:
    equipment = []
    if re.search(r"iphone|iPhone", text):
        equipment.append("iPhone")
    if "手机" in text and "iPhone" not in equipment:
        equipment.append("手机")
    if "相机" in text:
        equipment.append("相机")
    for lens in re.findall(r"\d{2,3}\s?mm", text, flags=re.IGNORECASE):
        equipment.append(lens.replace(" ", ""))
    return equipment or ["手机"]


def _same_city(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return left.replace("市", "") == right.replace("市", "")


def _merge_llm_goal(base: dict[str, Any], llm_goal: dict[str, Any] | None, user_input: str) -> dict[str, Any]:
    if not llm_goal:
        return base
    merged = dict(base)
    base_destination = base.get("destination")
    list_keys = {"shooting_style", "visual_elements", "subject", "equipment", "platform", "constraints"}
    for key in [
        "destination",
        "departure_city",
        "date_range",
        "shooting_style",
        "visual_elements",
        "subject",
        "equipment",
        "platform",
        "budget",
        "constraints",
    ]:
        value = llm_goal.get(key)
        if value not in (None, "", [], {}):
            if key == "destination" and base_destination and base_destination != "待推荐":
                text_value = str(value)
                if not _same_city(base_destination, text_value) and text_value not in user_input:
                    continue
            if key in list_keys:
                merged[key] = list(dict.fromkeys([*(_ensure_list(base.get(key), [])), *(_ensure_list(value, []))]))
            elif key == "date_range" and "周末" in user_input:
                base_dates = base.get("date_range") if isinstance(base.get("date_range"), list) else []
                llm_dates = value if isinstance(value, list) else [value]
                merged[key] = base_dates if len(base_dates) > len(llm_dates) else value
            else:
                merged[key] = value
    return merged


def _ensure_list(value: Any, fallback: list[Any]) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, "", {}):
        return fallback
    return [value]


def _normalize_goal_shapes(goal: dict[str, Any], user_input: str) -> dict[str, Any]:
    normalized = dict(goal)
    date_range = normalized.get("date_range")
    if isinstance(date_range, str):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_range):
            normalized["date_range"] = [date_range]
        else:
            normalized["date_range"] = parse_date_range(user_input)
    elif isinstance(date_range, list):
        iso_dates = [item for item in date_range if isinstance(item, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", item)]
        normalized["date_range"] = iso_dates or parse_date_range(user_input)
    else:
        normalized["date_range"] = parse_date_range(user_input)

    for key in ["shooting_style", "visual_elements", "subject", "equipment", "platform", "constraints", "missing_fields"]:
        normalized[key] = _ensure_list(normalized.get(key), goal.get(key) if isinstance(goal.get(key), list) else [])
    return normalized


def _should_use_llm(parsed_goal: dict[str, Any], user_input: str) -> bool:
    mode = settings.llm_goal_parser_mode.lower().strip()
    if mode in {"off", "false", "0", "disabled"}:
        return False
    if mode in {"always", "true", "1"}:
        return True
    if mode == "auto":
        return True
    if parsed_goal.get("missing_fields"):
        return True
    if parsed_goal.get("shooting_style") == ["自然旅拍"]:
        return True
    if parsed_goal.get("visual_elements") == ["自然光", "环境人像"]:
        return True
    destination = parsed_goal.get("destination")
    if destination == "杭州" and not any(keyword in user_input for keyword in ["杭州", "西湖", "北山街", "柳浪闻莺", "曲院风荷"]):
        return True
    return False


def parse_goal(
    user_input: str,
    use_llm: bool = True,
    reference_images: list[str] | None = None,
) -> tuple[dict[str, Any], list[str], bool]:
    has_explicit_destination = has_destination_signal(user_input)
    destination = infer_city(user_input)
    departure_city = infer_departure_city(user_input, destination)
    shooting_style = [item for item in STYLE_KEYWORDS if item in user_input]
    visual_elements = [item for item in VISUAL_KEYWORDS if item in user_input]
    platform = [item for item in PLATFORM_KEYWORDS if item in user_input]

    parsed_goal: dict[str, Any] = {
        "destination": destination,
        "departure_city": departure_city,
        "date_range": parse_date_range(user_input),
        "shooting_style": shooting_style or ["自然旅拍"],
        "visual_elements": visual_elements or ["自然光", "环境人像"],
        "subject": ["人像"] if any(word in user_input for word in ["人像", "旅拍", "写真", "拍照"]) else ["旅拍"],
        "equipment": _find_equipment(user_input),
        "platform": platform,
        "budget": None,
        "constraints": [],
        "missing_fields": [],
        "raw_text": user_input,
    }

    warnings: list[str] = []
    if not has_explicit_destination and destination == COASTAL_DEFAULT_CITY:
        warnings.append(f"未指定目的地，已按海边旅拍默认选择{COASTAL_DEFAULT_CITY}；如需其他城市，请在需求中说明。")
        parsed_goal["destination_inferred_from"] = "coastal_intent_default"
    elif not has_explicit_destination and destination == "待推荐":
        warnings.append("未指定明确目的地，会先按风格在机位库与外部工具中寻找可核验地点。")
        parsed_goal["destination_inferred_from"] = "needs_recommendation"

    if not departure_city and "从" in user_input:
        parsed_goal["missing_fields"].append("出发城市")
    if not shooting_style:
        parsed_goal["missing_fields"].append("偏好的视觉风格")

    llm_used = False
    if use_llm and is_llm_configured() and _should_use_llm(parsed_goal, user_input):
        llm_goal, warning = complete_json_multimodal(
            "你是旅拍助手的多模态 Goal Parser。只输出 JSON，不要输出解释。",
            (
                "请把用户需求解析为 JSON，字段包括 destination, departure_city, date_range, "
                "shooting_style, visual_elements, subject, equipment, platform, budget, constraints, missing_fields。\n"
                "如果有参考图，请同时识别图片里的地点线索、场景类型、光线、构图、动作和风格，并把可用于规划的结果合并进字段。"
                "不要编造具体地点；看不出来就写入 constraints 或 missing_fields。\n"
                f"用户输入：{user_input}"
            ),
            reference_images,
        )
        if warning:
            warnings.append(warning)
        if llm_goal:
            parsed_goal = _merge_llm_goal(parsed_goal, llm_goal, user_input)
            parsed_goal = _normalize_goal_shapes(parsed_goal, user_input)
            llm_used = True
    else:
        parsed_goal = _normalize_goal_shapes(parsed_goal, user_input)

    return parsed_goal, warnings, llm_used


def goal_parser_node(state: AgentState) -> AgentState:
    if state.get("parsed_goal"):
        return {
            "parsed_goal": state["parsed_goal"],
            "warnings": state.get("warnings") or [],
            "llm_used": bool(state.get("llm_used")),
            "llm_call_count": int(state.get("llm_call_count") or 0),
        }

    parsed_goal, warnings, llm_used = parse_goal(
        state["user_input"],
        use_llm=True,
        reference_images=state.get("reference_images") or [],
    )
    return {
        "parsed_goal": parsed_goal,
        "warnings": [*(state.get("warnings") or []), *warnings],
        "llm_used": llm_used,
        "llm_call_count": int(state.get("llm_call_count") or 0) + (1 if llm_used else 0),
    }
