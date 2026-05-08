from __future__ import annotations

from typing import Any

from app.agent.state import AgentState
from app.spot.cities import get_city_profile


def _line_items(items: Any) -> str:
    if not items:
        return "待确认"
    if isinstance(items, str):
        return items
    if isinstance(items, int | float):
        return str(items)
    if isinstance(items, list):
        values = [str(item) for item in items if item is not None and str(item).strip()]
        return "、".join(values) if values else "待确认"
    return str(items)


def _sentence_items(items: Any) -> str:
    if not isinstance(items, list):
        return _line_items(items)
    values = [str(item).strip().rstrip("。") for item in items if item is not None and str(item).strip()]
    return "；".join(values) if values else "待确认"


def _status_label(status: str | None) -> str:
    labels = {
        "ok": "未发现冲突",
        "at_risk": "存在风险",
        "invalid": "需要调整",
    }
    return labels.get(status or "ok", status or "未发现冲突")


def _action_label(action: str | None) -> str:
    labels = {
        "continue": "继续执行",
        "minor_adjust": "轻量调整",
        "rerank_options": "重排候选项",
        "replan_route": "重排路线",
    }
    return labels.get(action or "continue", action or "继续执行")


def _transport_options_text(transfer: dict[str, Any]) -> str:
    options = transfer.get("travel_options") or []
    pieces = []
    for option in options[:3]:
        label = option.get("mode_label") or option.get("mode")
        minutes = option.get("duration_minutes")
        if not label or minutes is None:
            continue
        pieces.append(f"{label}{minutes}分钟")
    return "、".join(pieces)


def _arrival_recommendation(parsed_goal: dict[str, Any], route: list[dict[str, Any]]) -> str:
    destination = parsed_goal.get("destination") or "杭州"
    departure = parsed_goal.get("departure_city")
    profile = get_city_profile(destination)
    first_start = route[0]["start_time"] if route else profile["default_start_time"]
    if departure == "上海" and destination == "杭州":
        return f"建议 13:30 前到达{profile['arrival_station']}，这样能赶上 {first_start} 左右的第一段拍摄。"
    if departure:
        return f"建议在第一段拍摄前至少 90 分钟抵达{profile['arrival_station']}；{profile['arrival_note']}"
    return f"建议在 {first_start} 前至少 60-90 分钟到达第一机位附近，给换装、补妆和找背景留缓冲。"


def format_markdown(state: AgentState) -> str:
    parsed_goal = state["parsed_goal"]
    visual_goal = state["visual_goal"]
    weather = state["weather_context"]
    sunlight = state["sunlight_context"]
    map_context = state.get("map_context") or {}
    reference_context = state.get("reference_context") or {}
    discovery_context = state.get("discovery_context") or {}
    image_analysis = state.get("image_analysis") or {}
    repair_context = state.get("repair_context") or {}
    route = state["optimized_route"]
    backup_plan = state["backup_plan"]
    warnings = state.get("warnings") or []

    destination = parsed_goal.get("destination", "目的地")
    dates = parsed_goal.get("date_range") or []
    styles = parsed_goal.get("shooting_style") or []
    elements = parsed_goal.get("visual_elements") or []
    equipment = parsed_goal.get("equipment") or []

    lines = [
        f"# {destination}旅拍初始方案",
        "",
        "## 1. 用户目标总结",
        f"- 目的地：{destination}",
        f"- 日期：{_line_items(dates)}",
        f"- 风格：{_line_items(styles)}",
        f"- 核心画面：{_line_items(elements)}",
        f"- 器材：{_line_items(equipment)}",
        f"- 视觉主目标：{visual_goal.get('primary_goal')}",
        "",
        "## 2. 推荐到达时间",
        _arrival_recommendation(parsed_goal, route),
        "",
        "## 3. 天气和光线判断",
        f"- 天气：{weather.get('summary')}",
        f"- 建议：{weather.get('shooting_advice')}",
            f"- 光线：{sunlight.get('summary')}",
    ]

    if image_analysis:
        reference_image = visual_goal.get("reference_image") or {}
        lines.extend(
            [
                "",
                "## 4. 参考图复刻要点",
                f"- 风格/氛围：{image_analysis.get('style_summary') or image_analysis.get('description') or reference_image.get('style_summary') or '已纳入视觉目标'}",
                f"- 光线：{_line_items(image_analysis.get('lighting') or reference_image.get('lighting'))}",
                f"- 构图：{_line_items(image_analysis.get('composition') or reference_image.get('composition'))}",
                f"- 动作：{_line_items(image_analysis.get('pose_action') or image_analysis.get('poses') or reference_image.get('pose_action'))}",
                f"- 可复刻地点类型：{_line_items(image_analysis.get('possible_location_types') or image_analysis.get('location_types') or reference_image.get('possible_location_types'))}",
            ]
        )
    tool_section_index = 5 if image_analysis else 4

    risk_flags = weather.get("risk_flags") or []
    if risk_flags:
        lines.append(f"- 风险：{_line_items(risk_flags)}")

    transfers = map_context.get("route_transfers") or []
    transfer_summaries = [item.get("summary") for item in transfers if item.get("summary")]
    geo_summary = map_context.get("geo_summary") or {}
    references = reference_context.get("results") or []
    repair_evaluation = repair_context.get("evaluation") or {}
    repair_status = repair_evaluation.get("status") or "ok"
    repair_action = repair_evaluation.get("recommended_action") or "continue"

    lines.extend(
        [
            "",
            f"## {tool_section_index}. 工具依据与修复判断",
            (
                "- 点位发现：先查内置机位库，再由 LLM 判断是否需要外部工具；"
                f"高德 POI 搜索 {len(discovery_context.get('map_poi_searches') or [])} 次，"
                f"跳过泛词/已命中搜索 {len(discovery_context.get('skipped_map_requests') or [])} 次。"
            ),
            (
                "- 地图路线：使用机位库经纬度规划移动；"
                f"路线机位坐标 {geo_summary.get('geo_verified_count', 0)}/{geo_summary.get('route_spot_count', len(route))} 已验证，"
                f"路线移动 {len(transfers)} 段。"
            ),
            f"- 参考搜索：{len(references)} 条公开参考，只作为标题、链接和摘要线索。",
            f"- 冲突检查：{_status_label(repair_status)}；建议动作：{_action_label(repair_action)}。",
        ]
    )
    if transfer_summaries:
        lines.append(f"- 移动摘要：{_sentence_items(transfer_summaries[:3])}。")
    issue_messages = [item.get("message") for item in repair_evaluation.get("issues") or [] if item.get("message")]
    if issue_messages:
        lines.append(f"- 触发原因：{_line_items(issue_messages[:3])}")
    llm_review = repair_context.get("llm_review") or {}
    if repair_context.get("llm_used"):
        applied_text = "已应用" if repair_context.get("applied") else "未改动路线"
        decision = _action_label(llm_review.get("decision"))
        lines.append(f"- LLM 修复：{applied_text}；决策：{decision}；证据：{_line_items(llm_review.get('evidence_refs') or [])}")
    else:
        lines.append("- LLM 修复：未触发或不可用，当前结果由规则和工具结果生成。")
    if llm_review.get("user_facing_warning"):
        lines.append(f"- 面向执行提醒：{llm_review['user_facing_warning']}")

    if discovery_context.get("location_mentions"):
        mention_text = [
            f"{item.get('raw_text')}→{item.get('search_query')}"
            for item in discovery_context.get("location_mentions") or []
            if isinstance(item, dict)
        ]
        lines.append(f"- 点位理解：{_line_items(mention_text[:5])}")

    route_dates = list(dict.fromkeys(item.get("date") for item in route if item.get("date")))
    lines.extend(["", f"## {tool_section_index + 1}. 拍摄路线"])
    current_date = None
    for item in route:
        if len(route_dates) > 1 and item.get("date") != current_date:
            current_date = item.get("date")
            lines.append(f"### {current_date}")
        transfer = item.get("transfer_to_next")
        transfer_text = ""
        if transfer:
            option_text = _transport_options_text(transfer)
            alternative_text = f"；可选：{option_text}" if option_text else ""
            transfer_text = f"｜下一段移动：{transfer.get('summary')}{alternative_text}"
        lines.append(
            f"- {item['start_time']}-{item['end_time']}｜{item['spot_name']}｜"
            f"{item['shoot_goal']}｜评分 {item.get('final_score')}{transfer_text}"
        )

    lines.extend(["", f"## {tool_section_index + 2}. 机位级拍摄指导"])
    for item in route:
        guide = item.get("guide") or {}
        lines.extend(
            [
                f"### {item['start_time']}-{item['end_time']} {item['spot_name']}",
                f"- 拍摄目标：{item['shoot_goal']}",
                f"- 人物站位：{guide.get('subject_position')}",
                f"- 摄影师站位：{guide.get('photographer_position')}",
                f"- 焦段/器材：{guide.get('lens')}；{guide.get('equipment_tip')}",
                f"- 动作清单：{_line_items(guide.get('poses') or [])}",
                f"- 构图提示：{_line_items(guide.get('composition_notes') or [])}",
                f"- 现场注意：{guide.get('safety_notes')}",
            ]
        )

    lines.extend(["", f"## {tool_section_index + 3}. 备用方案"])
    for item in backup_plan:
        lines.append(f"- {item['trigger']}：{item['action']}")

    if references:
        lines.extend(["", f"## {tool_section_index + 4}. 参考信息"])
        for item in references[:5]:
            title = item.get("title") or "参考来源"
            url = item.get("url") or ""
            lines.append(f"- {title}：{url}")

    if warnings:
        warning_index = tool_section_index + 5 if references else tool_section_index + 4
        lines.extend(["", f"## {warning_index}. 不确定性"])
        for warning in warnings:
            lines.append(f"- {warning}")

    return "\n".join(lines)


def final_formatter_node(state: AgentState) -> AgentState:
    return {"final_markdown": format_markdown(state)}
