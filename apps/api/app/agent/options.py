from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def _parse_time(day: str, value: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{value}:00")


def _fmt(value: datetime) -> str:
    return value.strftime("%H:%M")


def _slots_for_daily(daily: dict[str, Any]) -> list[dict[str, str]]:
    day = daily.get("date")
    if not day:
        day = datetime.now().date().isoformat()

    evening = (daily.get("golden_hours") or [{}, {"start": "17:00", "end": "18:20"}])[1]
    blue = daily.get("blue_hour") or {"start": "18:30", "end": "19:00"}
    sunrise = daily.get("sunrise") or "06:00"
    sunset = daily.get("sunset") or "18:30"

    morning_start = _parse_time(day, sunrise) + timedelta(minutes=30)
    morning_end = morning_start + timedelta(minutes=75)
    sunset_start = _parse_time(day, sunset) - timedelta(minutes=45)
    sunset_end = _parse_time(day, sunset) + timedelta(minutes=10)

    return [
        {"slot_type": "morning", "date": day, "start": _fmt(morning_start), "end": _fmt(morning_end), "light_label": "清晨柔光"},
        {"slot_type": "afternoon", "date": day, "start": "15:00", "end": "16:10", "light_label": "下午柔光"},
        {"slot_type": "transition", "date": day, "start": "16:20", "end": "17:05", "light_label": "傍晚过渡光"},
        {"slot_type": "golden", "date": day, "start": evening.get("start", "17:00"), "end": evening.get("end", "18:15"), "light_label": "黄金时刻"},
        {"slot_type": "sunset", "date": day, "start": _fmt(sunset_start), "end": _fmt(sunset_end), "light_label": "日落窗口"},
        {"slot_type": "blue", "date": day, "start": blue.get("start", "18:30"), "end": blue.get("end", "19:00"), "light_label": "蓝调时刻"},
    ]


def _build_slots(sunlight_context: dict[str, Any]) -> list[dict[str, str]]:
    daily_items = sunlight_context.get("daily") or [{}]
    slots: list[dict[str, str]] = []
    for daily in daily_items:
        slots.extend(_slots_for_daily(daily if isinstance(daily, dict) else {}))
    return slots


def _pick_slots_for_spot(spot: dict[str, Any], visual_goal: dict[str, Any], slots: list[dict[str, str]]) -> list[dict[str, str]]:
    haystack = f"{spot['name']} {' '.join(spot.get('themes') or [])} {' '.join(spot.get('best_time_hint') or [])}"
    wanted = set(visual_goal.get("must_have_elements") or []) | set(visual_goal.get("optional_elements") or [])
    selected_types: list[str] = []
    wants_sunrise = bool({"日出", "清晨", "晨光"}.intersection(wanted))
    wants_sunset = bool({"日落", "夕阳", "晚霞", "逆光"}.intersection(wanted))
    if wants_sunrise or any(item in haystack for item in ["日出", "清晨", "晨光"]):
        selected_types.append("morning")
    if wants_sunset or any(item in haystack for item in ["日落", "夕阳", "傍晚"]):
        selected_types.extend(["golden", "sunset"])
    if any(item in haystack for item in ["蓝调", "夜景"]):
        selected_types.append("blue")
    if any(item in haystack for item in ["树", "公园", "湖", "海", "街"]) or not selected_types:
        selected_types.extend(["afternoon", "transition", "golden"])

    unique_types = list(dict.fromkeys(selected_types))[:4]
    return [slot for slot in slots if slot["slot_type"] in unique_types]


def _shoot_goal(spot: dict[str, Any], slot: dict[str, str], visual_goal: dict[str, Any]) -> str:
    must = visual_goal.get("must_have_elements") or ["环境人像"]
    if slot["slot_type"] == "sunset":
        return f"{spot['name']}的夕阳/逆光人像"
    if slot["slot_type"] == "blue":
        return f"{spot['name']}蓝调氛围人像"
    if slot["slot_type"] == "morning":
        return f"{spot['name']}清晨低人流画面"
    return f"{spot['name']}的{must[0]}自然旅拍"


def generate_spot_time_options(
    candidate_spots: list[dict[str, Any]],
    visual_goal: dict[str, Any],
    weather_context: dict[str, Any],
    sunlight_context: dict[str, Any],
) -> list[dict[str, Any]]:
    slots = _build_slots(sunlight_context)
    options: list[dict[str, Any]] = []
    option_index = 1
    for spot in candidate_spots[:10]:
        for slot in _pick_slots_for_spot(spot, visual_goal, slots):
            expected_visual = list(
                dict.fromkeys(
                    [
                        *(visual_goal.get("must_have_elements") or []),
                        *(spot.get("visual_elements") or [])[:3],
                    ]
                )
            )[:6]
            risks = []
            if spot.get("crowd_risk") == "high":
                risks.append("热门机位人流较高")
            if (weather_context.get("max_precipitation_probability") or 0) >= 50:
                risks.append("降水可能影响户外拍摄")
            if slot["slot_type"] in {"sunset", "golden"} and (weather_context.get("avg_cloud_cover") or 0) >= 75:
                risks.append("云量偏高，夕阳可能不稳定")

            options.append(
                {
                    "option_id": f"opt_{option_index:03d}",
                    "spot_id": spot["spot_id"],
                    "spot_name": spot["name"],
                    "date": slot["date"],
                    "start_time": slot["start"],
                    "end_time": slot["end"],
                    "time_window": f"{slot['start']}-{slot['end']}",
                    "slot_type": slot["slot_type"],
                    "light_label": slot["light_label"],
                    "shoot_goal": _shoot_goal(spot, slot, visual_goal),
                    "expected_visual": expected_visual,
                    "risks": risks or ["注意现场人流和背景干扰"],
                    "recommended_shots": [
                        "自然慢走",
                        "回头看镜头",
                        "低头整理头发或裙摆",
                        "环境留白半身",
                    ],
                    "spot": spot,
                }
            )
            option_index += 1

    return options
