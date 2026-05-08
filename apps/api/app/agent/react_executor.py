from __future__ import annotations

from typing import Any

from app.agent.discovery import analyze_request_for_discovery, execute_discovery_tools
from app.agent.options import generate_spot_time_options
from app.agent.plan_repair import repair_plan_if_needed
from app.agent.state import AgentState
from app.agent.visual_goal import build_visual_goal
from app.core.config import settings
from app.planning.route_optimizer import build_backup_plan, optimize_route
from app.scoring.spot_time_scoring import score_spot_time_options
from app.spot.repository import search_candidate_spots
from app.tools.map import route_options
from app.tools.sunlight import build_sunlight_context
from app.tools.weather import fetch_weather_context


def _step(
    task_id: str,
    reasoning_summary: str,
    tool_name: str | None,
    tool_input: dict[str, Any],
    observation: dict[str, Any] | list[Any],
    tool_output: dict[str, Any] | list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "step_type": "react_tool_call" if tool_name else "state_update",
        "reasoning_summary": reasoning_summary,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output": tool_output if tool_output is not None else observation,
        "observation": observation,
    }


def _warning_from_tool(result: dict[str, Any], default: str) -> str | None:
    if result.get("success"):
        return None
    error = result.get("error")
    return error or default


def _route_transfer_context(route: list[dict[str, Any]]) -> dict[str, Any]:
    for item in route:
        item.pop("transfer_to_next", None)
    transfers = []
    for index in range(len(route) - 1):
        current = route[index]
        nxt = route[index + 1]
        if current.get("date") != nxt.get("date"):
            continue
        result = route_options(current, nxt)
        data = result.get("data") or {}
        recommended = data.get("recommended") or {}
        transfer = {
            "from": current.get("spot_name"),
            "to": nxt.get("spot_name"),
            "success": result.get("success"),
            "source": recommended.get("source") or result.get("source"),
            "mode": recommended.get("mode"),
            "mode_label": recommended.get("mode_label"),
            "distance_m": recommended.get("distance_m"),
            "duration_seconds": recommended.get("duration_seconds"),
            "duration_minutes": recommended.get("duration_minutes"),
            "summary": recommended.get("summary"),
            "recommendation_reason": recommended.get("recommendation_reason"),
            "travel_options": data.get("options") or [],
            "error": result.get("error"),
        }
        current["transfer_to_next"] = transfer
        transfers.append(transfer)
    geo_verified_count = sum(1 for item in route if item.get("geo_verified"))
    return {
        "geo_summary": {
            "route_spot_count": len(route),
            "geo_verified_count": geo_verified_count,
            "missing_geo_count": max(len(route) - geo_verified_count, 0),
            "coordinate_source": "photo_spots.lat_lng",
        },
        "route_transfers": transfers,
        "transfer_count": len(transfers),
    }


def react_executor_node(state: AgentState) -> AgentState:
    parsed_goal = dict(state["parsed_goal"])
    warnings = list(state.get("warnings") or [])
    steps = list(state.get("agent_steps") or [])
    llm_call_count = int(state.get("llm_call_count") or 0)
    reference_images = state.get("reference_images") or []

    preset_candidate_spots = state.get("candidate_spots")
    if preset_candidate_spots is None:
        candidate_spots = search_candidate_spots(parsed_goal)
        spot_source = "jsonl_seed_fallback"
    else:
        candidate_spots = preset_candidate_spots
        spot_source = state.get("candidate_spots_source") or "postgresql"
    internal_candidate_count = len(candidate_spots)
    steps.append(
        _step(
            "candidate_discovery",
            "需要把用户风格与城市机位库对齐，先查询持久化机位表。",
            "photo_spots_repository",
            {"destination": parsed_goal.get("destination"), "styles": parsed_goal.get("shooting_style")},
            {
                "source": spot_source,
                "count": len(candidate_spots),
                "geo_verified_count": sum(1 for item in candidate_spots if item.get("geo_verified")),
                "top_spots": [item["name"] for item in candidate_spots[:5]],
            },
        )
    )
    if not candidate_spots:
        warnings.append("未找到候选机位，当前方案无法生成完整路线。")

    allow_llm_analysis = llm_call_count < settings.agent_max_llm_calls
    request_analysis, analysis_warning = analyze_request_for_discovery(
        user_input=state["user_input"],
        parsed_goal=parsed_goal,
        reference_images=reference_images,
        internal_spots=candidate_spots,
        allow_llm=allow_llm_analysis,
    )
    if analysis_warning:
        warnings.append(analysis_warning)
    if request_analysis.get("llm_used"):
        llm_call_count += 1
    elif not allow_llm_analysis:
        warnings.append(f"已达到 AGENT_MAX_LLM_CALLS={settings.agent_max_llm_calls}，跳过额外 LLM 点位规划。")
    steps.append(
        _step(
            "multimodal_request_analysis",
            "需要先让 LLM 统一理解文字、参考图、点位意图和工具调用边界。",
            "multimodal_llm.request_planner" if request_analysis.get("llm_used") else None,
            {"image_count": len(reference_images), "internal_candidate_count": len(candidate_spots)},
            {
                "llm_used": request_analysis.get("llm_used"),
                "location_mentions": len(request_analysis.get("location_mentions") or []),
                "external_tool_requests": len(request_analysis.get("external_tool_requests") or []),
                "image_analysis_available": bool(request_analysis.get("image_analysis")),
            },
            request_analysis,
        )
    )

    discovery_result = execute_discovery_tools(
        parsed_goal=parsed_goal,
        internal_spots=candidate_spots,
        analysis=request_analysis,
    )
    candidate_spots = discovery_result["candidate_spots"]
    reference_context = discovery_result["reference_context"]
    warnings.extend(discovery_result.get("warnings") or [])
    image_analysis = request_analysis.get("image_analysis") if isinstance(request_analysis.get("image_analysis"), dict) else {}
    discovery_context = {
        "intent_summary": request_analysis.get("intent_summary"),
        "location_mentions": request_analysis.get("location_mentions") or [],
        "external_tool_requests": request_analysis.get("external_tool_requests") or [],
        "skipped_map_requests": discovery_result.get("skipped_map_requests") or [],
        "map_poi_searches": [
            {
                "request": item.get("request"),
                "success": (item.get("result") or {}).get("success"),
                "error": (item.get("result") or {}).get("error"),
                "count": len((((item.get("result") or {}).get("data") or {}).get("pois") or [])),
            }
            for item in discovery_result.get("map_poi_searches") or []
        ],
        "reference_searches": [
            {
                "request": item.get("request"),
                "success": (item.get("result") or {}).get("success"),
                "error": (item.get("result") or {}).get("error"),
                "count": len((((item.get("result") or {}).get("data") or {}).get("results") or [])),
            }
            for item in discovery_result.get("reference_searches") or []
        ],
        "candidate_count_before_fusion": internal_candidate_count,
        "candidate_count_after_fusion": len(candidate_spots),
        "image_analysis_available": bool(image_analysis),
    }
    map_context: dict[str, Any] = {
        "poi_searches": discovery_context["map_poi_searches"],
        "skipped_poi_searches": discovery_context["skipped_map_requests"],
    }
    if parsed_goal.get("destination") not in {"杭州", "青岛", "厦门", "北京", "南京", "三亚"} and candidate_spots:
        parsed_goal["destination"] = candidate_spots[0].get("city") or parsed_goal.get("destination")

    for index, item in enumerate(discovery_result.get("map_poi_searches") or [], start=1):
        result = item.get("result") or {}
        data = result.get("data") or {}
        steps.append(
            _step(
                f"map_poi_search_{index}",
                "只有在用户明确点位未命中内置库或命中但缺坐标时，才调用高德关键词搜索。",
                "map_tool.amap_poi_search",
                item.get("request") or {},
                {
                    "success": result.get("success"),
                    "query": data.get("query"),
                    "count": len(data.get("pois") or []),
                    "error": result.get("error"),
                },
                result,
            )
        )
    for index, item in enumerate(discovery_result.get("reference_searches") or [], start=1):
        result = item.get("result") or {}
        data = result.get("data") or {}
        steps.append(
            _step(
                f"reference_search_{index}",
                "在机位库不足或 LLM 认为需要查证公开内容时，调用 Tavily 只提取标题、链接和摘要。",
                "search_tool.tavily_search",
                item.get("request") or {},
                {
                    "success": result.get("success"),
                    "query": data.get("query"),
                    "result_count": len(data.get("results") or []),
                    "error": result.get("error"),
                },
                result,
            )
        )
    steps.append(
        _step(
            "spot_fusion",
            "需要合并内置机位、地图 POI 和参考线索，并去重保留可信来源。",
            None,
            {
                "internal_count": internal_candidate_count,
                "map_poi_search_count": len(discovery_result.get("map_poi_searches") or []),
                "reference_search_count": len(discovery_result.get("reference_searches") or []),
            },
            {
                "candidate_count": len(candidate_spots),
                "geo_verified_count": sum(1 for item in candidate_spots if item.get("geo_verified")),
                "top_spots": [item["name"] for item in candidate_spots[:5]],
            },
        )
    )

    weather_context = fetch_weather_context(parsed_goal)
    steps.append(
        _step(
            "weather_lookup",
            "需要判断出发风险、云量和降水对夕阳/户外机位的影响。",
            "weather_tool",
            {"destination": parsed_goal.get("destination"), "date_range": parsed_goal.get("date_range")},
            {
                "status": weather_context.get("status"),
                "summary": weather_context.get("summary"),
                "risk_flags": weather_context.get("risk_flags"),
            },
        )
    )
    if weather_context.get("status") == "fallback":
        warnings.append(weather_context.get("summary", "天气工具使用兜底结果。"))

    sunlight_context = build_sunlight_context(parsed_goal)
    steps.append(
        _step(
            "sunlight_lookup",
            "需要为机位分配清晨、下午、黄金时刻、日落和蓝调窗口。",
            "sunlight_tool",
            {"destination": parsed_goal.get("destination"), "date_range": parsed_goal.get("date_range")},
            {"summary": sunlight_context.get("summary")},
        )
    )

    visual_goal = build_visual_goal(parsed_goal, image_analysis=image_analysis, reference_clues=reference_context.get("shooting_clues") or [])
    steps.append(
        _step(
            "visual_goal",
            "需要把风格词转成可评分的视觉元素和动作优先级。",
            None,
            {"parsed_goal": parsed_goal},
            {"primary_goal": visual_goal.get("primary_goal"), "must_have": visual_goal.get("must_have_elements")},
        )
    )

    options = generate_spot_time_options(candidate_spots, visual_goal, weather_context, sunlight_context)
    scored_options = score_spot_time_options(options, parsed_goal, visual_goal, weather_context)
    route = optimize_route(scored_options, parsed_goal)
    map_context.update(_route_transfer_context(route))
    missing_geo_count = (map_context.get("geo_summary") or {}).get("missing_geo_count") or 0
    if missing_geo_count:
        warnings.append("部分路线机位缺少精确经纬度，交通方案已使用缓冲时间兜底；可先运行 Nominatim seed 补坐标脚本。")
    backup_plan = build_backup_plan(parsed_goal, route, weather_context)
    route, backup_plan, repair_context, repair_warning = repair_plan_if_needed(
        parsed_goal=parsed_goal,
        candidate_spots=candidate_spots,
        weather_context=weather_context,
        sunlight_context=sunlight_context,
        map_context=map_context,
        reference_context=reference_context,
        route=route,
        backup_plan=backup_plan,
        allow_llm=llm_call_count < settings.agent_max_llm_calls,
    )
    if repair_context.get("llm_used"):
        llm_call_count += 1
    if repair_warning:
        warnings.append(repair_warning)
    if repair_context.get("applied"):
        map_context.update(_route_transfer_context(route))

    steps.append(
        _step(
            "spot_time_options",
            "需要把机位与时间窗口组合成候选项，再用规则评分排序。",
            "spot_time_option_generator",
            {"candidate_count": len(candidate_spots)},
            {"option_count": len(options), "top_score": scored_options[0]["final_score"] if scored_options else None},
        )
    )
    steps.append(
        _step(
            "route_optimizer",
            "需要选择不冲突、不过度绕路且保留关键光线窗口的一日路线。",
            "route_optimizer",
            {"option_count": len(scored_options)},
            {
                "route_count": len(route),
                "route_spots": [item["spot_name"] for item in route],
                "transfer_count": len(map_context.get("route_transfers") or []),
            },
        )
    )
    steps.append(
        _step(
            "transport_planning",
            "需要基于路线机位经纬度，对步行、骑行、打车和公交/地铁做多方式比较。",
            "map_tool.route_options",
            {"transfer_count": len(map_context.get("route_transfers") or []), "modes": ["walking", "bicycling", "taxi", "transit"]},
            {
                "transfer_count": len(map_context.get("route_transfers") or []),
                "recommended_modes": [
                    item.get("mode_label") for item in map_context.get("route_transfers") or [] if item.get("mode_label")
                ],
                "missing_geo_count": (map_context.get("geo_summary") or {}).get("missing_geo_count"),
            },
            map_context,
        )
    )
    steps.append(
        _step(
            "plan_conflict_repair",
            "需要判断工具结果是否打破原计划假设；如有冲突，才允许 LLM 基于证据做轻量修复。",
            "llm_plan_repair" if repair_context.get("llm_used") else None,
            {
                "evaluation_status": (repair_context.get("evaluation") or {}).get("status"),
                "recommended_action": (repair_context.get("evaluation") or {}).get("recommended_action"),
            },
            {
                "evaluation": repair_context.get("evaluation"),
                "llm_used": repair_context.get("llm_used"),
                "applied": repair_context.get("applied"),
                "review": repair_context.get("llm_review"),
            },
            repair_context,
        )
    )

    return {
        "parsed_goal": parsed_goal,
        "candidate_spots": candidate_spots,
        "weather_context": weather_context,
        "sunlight_context": sunlight_context,
        "map_context": map_context,
        "reference_context": reference_context,
        "discovery_context": discovery_context,
        "image_analysis": image_analysis,
        "repair_context": repair_context,
        "visual_goal": visual_goal,
        "spot_time_options": scored_options,
        "scored_options": scored_options,
        "optimized_route": route,
        "backup_plan": backup_plan,
        "agent_steps": steps,
        "warnings": list(dict.fromkeys(warnings)),
        "llm_used": bool(state.get("llm_used")) or bool(request_analysis.get("llm_used")) or bool(repair_context.get("llm_used")),
        "llm_call_count": llm_call_count,
    }
