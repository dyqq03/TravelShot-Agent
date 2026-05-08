from __future__ import annotations

from typing import Any


def _clamp(value: float) -> float:
    return round(max(0, min(10, value)), 1)


def score_spot_time_options(
    options: list[dict[str, Any]],
    parsed_goal: dict[str, Any],
    visual_goal: dict[str, Any],
    weather_context: dict[str, Any],
) -> list[dict[str, Any]]:
    desired_styles = set(parsed_goal.get("shooting_style") or [])
    desired_elements = set(visual_goal.get("must_have_elements") or []) | set(parsed_goal.get("visual_elements") or [])
    max_precip = weather_context.get("max_precipitation_probability") or 0
    avg_cloud = weather_context.get("avg_cloud_cover") or 50
    max_wind = weather_context.get("max_wind_speed") or 0
    scored: list[dict[str, Any]] = []

    for index, option in enumerate(options):
        spot = option.get("spot") or {}
        spot_styles = set(spot.get("suitable_styles") or [])
        option_elements = set(option.get("expected_visual") or [])
        style_fit = 6.2 + len(desired_styles.intersection(spot_styles)) * 1.0 + spot.get("match_score", 7.5) * 0.15
        visual_element_fit = 6.0 + len(desired_elements.intersection(option_elements)) * 0.8

        slot_type = option.get("slot_type")
        light_fit = 7.2
        if slot_type in {"golden", "sunset"}:
            light_fit += 1.2
        if slot_type == "blue" and ("蓝调" in desired_styles or "夜景" in desired_styles):
            light_fit += 1.3
        if slot_type == "morning":
            light_fit += 0.4
        if slot_type == "transition":
            light_fit += 0.8
        if "夕阳" in desired_elements and slot_type == "sunset":
            light_fit += 1.2

        weather_fit = 8.0
        if max_precip >= 70:
            weather_fit -= 3.0
        elif max_precip >= 50:
            weather_fit -= 1.6
        if avg_cloud >= 75 and slot_type in {"sunset", "golden"}:
            weather_fit -= 1.2
        if avg_cloud <= 40 and ("蓝天" in desired_elements or slot_type == "sunset"):
            weather_fit += 0.6
        if max_wind >= 28 and spot.get("spot_type") == "海边":
            weather_fit -= 1.0

        transport_fit = 8.4 - min(index, 6) * 0.18
        if not spot.get("geo_verified"):
            transport_fit = min(transport_fit, 4.0)
        risk_score = 8.3
        if spot.get("crowd_risk") == "high":
            risk_score -= 1.0
        if not spot.get("geo_verified"):
            risk_score -= 0.8
        if option.get("risks"):
            risk_score -= min(len(option["risks"]), 3) * 0.25
        ticket_fit = 8.0 if spot.get("ticket_required") else 10.0
        constraint_fit = 9.0 if spot.get("phone_friendly", True) else 7.2

        final_score = (
            _clamp(style_fit) * 0.22
            + _clamp(visual_element_fit) * 0.18
            + _clamp(light_fit) * 0.22
            + _clamp(weather_fit) * 0.16
            + _clamp(transport_fit) * 0.08
            + _clamp(risk_score) * 0.06
            + _clamp(ticket_fit) * 0.04
            + _clamp(constraint_fit) * 0.04
        )
        if not spot.get("geo_verified"):
            final_score -= 0.6

        item = dict(option)
        item.update(
            {
                "style_fit": _clamp(style_fit),
                "visual_element_fit": _clamp(visual_element_fit),
                "light_fit": _clamp(light_fit),
                "weather_fit": _clamp(weather_fit),
                "transport_fit": _clamp(transport_fit),
                "risk_score": _clamp(risk_score),
                "ticket_fit": _clamp(ticket_fit),
                "constraint_fit": _clamp(constraint_fit),
                "final_score": round(final_score, 1),
            }
        )
        scored.append(item)

    scored.sort(key=lambda item: item["final_score"], reverse=True)
    return scored
