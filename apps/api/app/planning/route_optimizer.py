from __future__ import annotations

from datetime import datetime
from typing import Any

from app.spot.cities import get_city_profile


def _minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _overlaps(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return not (_minutes(left["end_time"]) + 10 <= _minutes(right["start_time"]) or _minutes(right["end_time"]) + 10 <= _minutes(left["start_time"]))


def _goal_terms(parsed_goal: dict[str, Any]) -> set[str]:
    values = set(parsed_goal.get("visual_elements") or []) | set(parsed_goal.get("shooting_style") or [])
    raw_text = str(parsed_goal.get("raw_text") or "")
    for term in ["日出", "清晨", "晨光", "日落", "夕阳", "晚霞", "长城"]:
        if term in raw_text:
            values.add(term)
    return values


def _is_requested_slot(option: dict[str, Any], terms: set[str]) -> bool:
    slot_type = option.get("slot_type")
    if slot_type == "morning" and {"日出", "清晨", "晨光"}.intersection(terms):
        return True
    if slot_type in {"golden", "sunset"} and {"日落", "夕阳", "晚霞"}.intersection(terms):
        return True
    return False


def _option_priority(option: dict[str, Any], terms: set[str]) -> tuple:
    spot = option.get("spot") or {}
    exact_match = bool(spot.get("exact_user_match"))
    return (
        1 if exact_match else 0,
        1 if _is_requested_slot(option, terms) else 0,
        1 if option.get("slot_type") == "sunset" else 0,
        1 if option.get("slot_type") == "golden" else 0,
        1 if spot.get("geo_verified") else 0,
        option.get("final_score", 0),
    )


def _select_day_options(
    day_options: list[dict[str, Any]],
    terms: set[str],
    max_items: int,
    global_used_spots: set[str],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used_spots: set[str] = set()
    slot_targets: list[str] = []
    if {"日出", "清晨", "晨光"}.intersection(terms):
        slot_targets.append("morning")
    elif max_items >= 3:
        slot_targets.append("morning")
    if {"日落", "夕阳", "晚霞"}.intersection(terms):
        slot_targets.append("sunset")

    for slot_type in slot_targets:
        for option in sorted(
            [item for item in day_options if item.get("slot_type") == slot_type],
            key=lambda item: _option_priority(item, terms),
            reverse=True,
        ):
            spot = option.get("spot") or {}
            spot_id = option["spot_id"]
            exact_match = bool(spot.get("exact_user_match"))
            if spot_id in used_spots and not exact_match:
                continue
            if spot_id in global_used_spots and not exact_match:
                continue
            if any(_overlaps(option, existing) for existing in selected):
                continue
            selected.append(option)
            used_spots.add(spot_id)
            if not exact_match:
                global_used_spots.add(spot_id)
            break

    for option in sorted(day_options, key=lambda item: _option_priority(item, terms), reverse=True):
        if len(selected) >= max_items:
            break
        spot = option.get("spot") or {}
        spot_id = option["spot_id"]
        exact_match = bool(spot.get("exact_user_match"))
        requested_slot = _is_requested_slot(option, terms)
        allow_repeat_same_spot = exact_match and requested_slot
        if spot_id in used_spots and not allow_repeat_same_spot:
            continue
        if spot_id in global_used_spots and not exact_match:
            continue
        if any(_overlaps(option, existing) for existing in selected):
            continue
        selected.append(option)
        used_spots.add(spot_id)
        if not exact_match:
            global_used_spots.add(spot_id)
        if len(selected) >= max_items:
            break
    return selected


def _build_guide(option: dict[str, Any], parsed_goal: dict[str, Any]) -> dict[str, Any]:
    spot = option.get("spot") or {}
    elements = set(spot.get("visual_elements") or []) | set(option.get("expected_visual") or [])
    equipment = parsed_goal.get("equipment") or ["手机"]
    lens = spot.get("recommended_lens_or_focal_length") or ("iPhone 1x/2x" if "iPhone" in equipment else "35mm-85mm")
    spot_type = spot.get("spot_type")

    if spot_type == "海边" or ("海边" in elements and spot_type not in {"湖边", "建筑", "公园", "街道"}):
        subject_position = "人物站在离水线安全距离外，身体与海岸线成 30-45 度，保留海面和天空留白。"
        photographer_position = "摄影师沿海岸线斜前方拍摄，优先顺风侧，避免让背景人群压住人物轮廓。"
    elif spot_type == "湖边" or "湖边" in elements:
        subject_position = "人物靠近湖边树荫或栏杆，身体与湖面成 45 度，背景保留水面层次。"
        photographer_position = "摄影师站在斜前方 3-5 米，略低机位，避开路人和杂乱岸线。"
    elif spot_type == "建筑" or "古建" in elements or "红墙" in elements:
        subject_position = "人物离墙面或建筑 1-2 米，避免贴墙；用门洞、柱线或台阶做框景。"
        photographer_position = "摄影师保持水平机位或轻微低机位，校正竖线，保留建筑秩序感。"
    else:
        subject_position = "人物站在画面三分线附近，和背景保持距离，留出可呼吸的环境空间。"
        photographer_position = "摄影师在斜前方或侧后方移动取景，优先找前景遮挡和干净背景。"

    if "iPhone" in equipment or "手机" in equipment:
        phone_tip = "iPhone 建议 1x 拍环境、2x 拍半身；逆光时曝光下拉 0.3-0.7 档。"
    else:
        phone_tip = "相机建议 35mm 拍环境、50/85mm 拍半身；注意保留高光细节。"

    return {
        "subject_position": subject_position,
        "photographer_position": photographer_position,
        "lens": lens,
        "equipment_tip": phone_tip,
        "poses": option.get("recommended_shots") or ["自然慢走", "回头", "低头整理头发"],
        "composition_notes": [
            "先拍一张环境建立，再拍半身情绪，最后补手部和裙摆细节。",
            "背景人多时降低机位或横向移动，用树、栏杆、墙面切掉杂乱区域。",
        ],
        "safety_notes": spot.get("access_and_notes") or "注意现场秩序和安全距离。",
    }


def optimize_route(
    scored_options: list[dict[str, Any]],
    parsed_goal: dict[str, Any],
    max_items: int = 4,
) -> list[dict[str, Any]]:
    terms = _goal_terms(parsed_goal)
    dates = list(dict.fromkeys(item.get("date") for item in scored_options if item.get("date")))
    if not dates:
        dates = parsed_goal.get("date_range") or []
    per_day_limit = max_items if len(dates) <= 1 else 3
    selected: list[dict[str, Any]] = []
    global_used_spots: set[str] = set()

    for day in dates:
        day_options = [item for item in scored_options if item.get("date") == day]
        selected.extend(_select_day_options(day_options, terms, per_day_limit, global_used_spots))

    if not selected and scored_options:
        selected = _select_day_options(scored_options, terms, max_items, global_used_spots)

    selected.sort(key=lambda item: (item.get("date", ""), _minutes(item["start_time"])))
    city = parsed_goal.get("destination") or "杭州"
    profile = get_city_profile(city)
    route: list[dict[str, Any]] = []
    for index, option in enumerate(selected, start=1):
        spot = option.get("spot") or {}
        route.append(
            {
                "item_id": f"route_{index:03d}",
                "option_id": option["option_id"],
                "sequence": index,
                "date": option.get("date") or datetime.now().date().isoformat(),
                "start_time": option["start_time"],
                "end_time": option["end_time"],
                "spot_name": option["spot_name"],
                "spot_id": option.get("spot_id"),
                "city": spot.get("city") or city,
                "spot_type": spot.get("spot_type"),
                "location_hint": spot.get("location_hint"),
                "lat": spot.get("lat"),
                "lng": spot.get("lng"),
                "geo_verified": bool(spot.get("geo_verified")),
                "shoot_goal": option["shoot_goal"],
                "light_label": option.get("light_label"),
                "final_score": option.get("final_score"),
                "route_note": (
                    "同片区机位之间预留 15-25 分钟移动和补妆缓冲；"
                    f"首次到达建议从 {profile['arrival_station']} 提前 90 分钟出发。"
                ),
                "guide": _build_guide(option, parsed_goal),
            }
        )
    return route


def build_backup_plan(
    parsed_goal: dict[str, Any],
    route: list[dict[str, Any]],
    weather_context: dict[str, Any],
) -> list[dict[str, Any]]:
    backup = [
        {
            "trigger": "晚到 40 分钟以内",
            "action": "压缩第一个机位，保留黄金时刻和日落机位。",
        },
        {
            "trigger": "晚到超过 60 分钟",
            "action": "直接去最高分的傍晚/日落机位，放弃中间过渡点。",
        },
    ]
    if (weather_context.get("max_precipitation_probability") or 0) >= 50:
        backup.append(
            {
                "trigger": "降雨增强",
                "action": "切换雨天电影感：找屋檐、咖啡店外立面、街道反光，减少海边/湖边暴露时间。",
            }
        )
    else:
        backup.append(
            {
                "trigger": "临时下雨",
                "action": "优先保留有树荫、屋檐或街道背景的机位，夕阳改为情绪半身和雨伞动作。",
            }
        )
    if route:
        backup.append(
            {
                "trigger": "人流过高",
                "action": f"在 {route[0]['spot_name']} 周边横向移动 50-100 米，找同类背景替代，不强占热门点。",
            }
        )
    return backup
