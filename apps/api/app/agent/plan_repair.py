from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from app.agent.llm import complete_json, is_llm_configured
from app.core.config import settings


def _minutes(value: str | None) -> int | None:
    if not value or ":" not in value:
        return None
    try:
        hour, minute = value.split(":", 1)
        return int(hour) * 60 + int(minute)
    except ValueError:
        return None


def _wants_sunset(parsed_goal: dict[str, Any]) -> bool:
    values = set(parsed_goal.get("visual_elements") or []) | set(parsed_goal.get("shooting_style") or [])
    return bool({"夕阳", "日落", "逆光"}.intersection(values))


def _outdoor_route_count(route: list[dict[str, Any]]) -> int:
    outdoor_types = {"海边", "湖边", "公园", "街道", "综合机位"}
    return sum(1 for item in route if item.get("spot_type") in outdoor_types)


def _mode_allows_llm_review() -> bool:
    mode = settings.llm_plan_repair_mode.lower().strip()
    return mode not in {"off", "false", "0", "disabled"}


def evaluate_plan_conflicts(
    *,
    parsed_goal: dict[str, Any],
    candidate_spots: list[dict[str, Any]],
    weather_context: dict[str, Any],
    map_context: dict[str, Any],
    route: list[dict[str, Any]],
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []

    if len(candidate_spots) < 3:
        issues.append(
            {
                "code": "too_few_candidate_spots",
                "severity": "invalid" if not candidate_spots else "at_risk",
                "evidence": {"candidate_count": len(candidate_spots)},
                "message": "候选机位数量不足，计划稳定性较低。",
            }
        )

    if len(route) < 2:
        issues.append(
            {
                "code": "route_too_short",
                "severity": "at_risk",
                "evidence": {"route_count": len(route)},
                "message": "路线段数偏少，可能无法覆盖用户目标。",
            }
        )

    max_precip = weather_context.get("max_precipitation_probability") or 0
    avg_cloud = weather_context.get("avg_cloud_cover") or 0
    outdoor_count = _outdoor_route_count(route)
    if max_precip >= 60 and outdoor_count:
        issues.append(
            {
                "code": "high_precipitation_outdoor_route",
                "severity": "at_risk",
                "evidence": {"max_precipitation_probability": max_precip, "outdoor_route_count": outdoor_count},
                "message": "降水概率较高，户外路线可能不稳定。",
            }
        )

    if _wants_sunset(parsed_goal) and avg_cloud >= 75:
        issues.append(
            {
                "code": "sunset_goal_cloud_risk",
                "severity": "at_risk",
                "evidence": {"avg_cloud_cover": avg_cloud, "wanted": "夕阳/日落"},
                "message": "用户想要夕阳画面，但云量偏高，夕阳窗口不确定。",
            }
        )

    for transfer in map_context.get("route_transfers") or []:
        current = next((item for item in route if item.get("spot_name") == transfer.get("from")), None)
        nxt = next((item for item in route if item.get("spot_name") == transfer.get("to")), None)
        if not current or not nxt:
            continue
        current_end = _minutes(current.get("end_time"))
        next_start = _minutes(nxt.get("start_time"))
        duration = transfer.get("duration_minutes")
        if current_end is None or next_start is None or duration is None:
            continue
        gap = next_start - current_end
        if gap >= 0 and duration + 10 > gap:
            issues.append(
                {
                    "code": "transfer_time_conflict",
                    "severity": "invalid",
                    "evidence": {
                        "from": transfer.get("from"),
                        "to": transfer.get("to"),
                        "available_gap_minutes": gap,
                        "duration_minutes": duration,
                        "source": transfer.get("source"),
                    },
                    "message": "两段拍摄之间移动时间不足。",
                }
            )

    status = "ok"
    if any(item["severity"] == "invalid" for item in issues):
        status = "invalid"
    elif issues:
        status = "at_risk"

    recommended_action = "continue"
    if status == "invalid":
        recommended_action = "replan_route"
    elif status == "at_risk":
        recommended_action = "minor_adjust"

    return {
        "status": status,
        "issues": issues,
        "recommended_action": recommended_action,
        "needs_llm_review": status != "ok" and _mode_allows_llm_review() and is_llm_configured(),
    }


def _compact_route(route: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "item_id": item.get("item_id"),
            "sequence": item.get("sequence"),
            "spot_name": item.get("spot_name"),
            "spot_type": item.get("spot_type"),
            "start_time": item.get("start_time"),
            "end_time": item.get("end_time"),
            "light_label": item.get("light_label"),
            "shoot_goal": item.get("shoot_goal"),
            "final_score": item.get("final_score"),
            "transfer_to_next": item.get("transfer_to_next"),
        }
        for item in route
    ]


def _compact_reference(reference_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "query": reference_context.get("query"),
        "results": [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "summary": item.get("summary"),
            }
            for item in (reference_context.get("results") or [])[:5]
        ],
    }


def _run_llm_review(
    *,
    parsed_goal: dict[str, Any],
    evaluator: dict[str, Any],
    weather_context: dict[str, Any],
    sunlight_context: dict[str, Any],
    map_context: dict[str, Any],
    reference_context: dict[str, Any],
    route: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    payload = {
        "parsed_goal": parsed_goal,
        "conflict_evaluation": evaluator,
        "weather_context": {
            "summary": weather_context.get("summary"),
            "risk_flags": weather_context.get("risk_flags"),
            "max_precipitation_probability": weather_context.get("max_precipitation_probability"),
            "avg_cloud_cover": weather_context.get("avg_cloud_cover"),
        },
        "sunlight_context": {
            "summary": sunlight_context.get("summary"),
            "daily": sunlight_context.get("daily"),
        },
        "map_context": {
            "route_transfers": map_context.get("route_transfers") or [],
            "geo_summary": map_context.get("geo_summary") or {},
        },
        "reference_context": _compact_reference(reference_context),
        "route": _compact_route(route),
    }
    return complete_json(
        "你是旅拍助手 Agent 的计划冲突修复器。只输出 JSON，不要输出解释。",
        (
            "请只根据输入中的工具结果和现有路线改善计划，严禁编造新机位、新天气、新地图耗时、新搜索结论。"
            "你不能新增 route item，只能从已有 route item 中选择保留或删除，并给出基于 evidence 的说明。"
            "输出 JSON 字段：decision, keep_route_item_ids, drop_route_item_ids, route_adjustment_notes, "
            "backup_plan_notes, user_facing_warning, confidence, evidence_refs。"
            "decision 只能是 continue/minor_adjust/rerank_options/replan_route。"
            "keep_route_item_ids 和 drop_route_item_ids 只能使用输入 route 中的 item_id。\n"
            f"输入：{json.dumps(payload, ensure_ascii=False)}"
        ),
    )


def _sanitize_review(review: dict[str, Any], route: list[dict[str, Any]], evaluator: dict[str, Any]) -> dict[str, Any]:
    allowed_ids = {item["item_id"] for item in route if item.get("item_id")}
    allowed_evidence = {item.get("code") for item in evaluator.get("issues") or [] if item.get("code")}
    decision = review.get("decision")
    if decision not in {"continue", "minor_adjust", "rerank_options", "replan_route"}:
        decision = "minor_adjust"

    keep_ids = [item for item in (review.get("keep_route_item_ids") or []) if item in allowed_ids]
    drop_ids = [item for item in (review.get("drop_route_item_ids") or []) if item in allowed_ids]
    if keep_ids:
        drop_ids = [item for item in drop_ids if item not in set(keep_ids)]
    if len(drop_ids) >= len(route):
        drop_ids = drop_ids[:-1]

    notes = review.get("route_adjustment_notes")
    if not isinstance(notes, list):
        notes = []
    backup_notes = review.get("backup_plan_notes")
    if not isinstance(backup_notes, list):
        backup_notes = []
    evidence_refs = review.get("evidence_refs")
    if not isinstance(evidence_refs, list):
        evidence_refs = []
    evidence_refs = [str(item) for item in evidence_refs if item in allowed_evidence]
    has_valid_evidence = bool(evidence_refs)

    confidence = review.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.5

    warning = review.get("user_facing_warning")
    if not isinstance(warning, str):
        warning = ""
    if not has_valid_evidence:
        drop_ids = []
        notes = []
        backup_notes = []
        warning = ""

    return {
        "decision": decision,
        "keep_route_item_ids": keep_ids,
        "drop_route_item_ids": drop_ids,
        "route_adjustment_notes": [str(item) for item in notes[:5]],
        "backup_plan_notes": [str(item) for item in backup_notes[:5]],
        "user_facing_warning": warning.strip(),
        "confidence": confidence,
        "evidence_refs": evidence_refs[:8],
    }


def _apply_review(
    route: list[dict[str, Any]],
    backup_plan: list[dict[str, Any]],
    review: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repaired_route = deepcopy(route)
    drop_ids = set(review.get("drop_route_item_ids") or [])
    if drop_ids:
        repaired_route = [item for item in repaired_route if item.get("item_id") not in drop_ids]
        for index, item in enumerate(repaired_route, start=1):
            item["sequence"] = index

    notes = review.get("route_adjustment_notes") or []
    if notes:
        note = "；".join(notes[:2])
        for item in repaired_route:
            item["route_note"] = f"{item.get('route_note') or ''} LLM修复建议：{note}".strip()

    repaired_backup = deepcopy(backup_plan)
    for note in review.get("backup_plan_notes") or []:
        repaired_backup.append({"trigger": "计划冲突修复", "action": note})
    return repaired_route, repaired_backup


def repair_plan_if_needed(
    *,
    parsed_goal: dict[str, Any],
    candidate_spots: list[dict[str, Any]],
    weather_context: dict[str, Any],
    sunlight_context: dict[str, Any],
    map_context: dict[str, Any],
    reference_context: dict[str, Any],
    route: list[dict[str, Any]],
    backup_plan: list[dict[str, Any]],
    allow_llm: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], str | None]:
    evaluator = evaluate_plan_conflicts(
        parsed_goal=parsed_goal,
        candidate_spots=candidate_spots,
        weather_context=weather_context,
        map_context=map_context,
        route=route,
    )
    repair_context: dict[str, Any] = {
        "evaluation": evaluator,
        "llm_used": False,
        "llm_review": None,
        "applied": False,
    }
    warning: str | None = None
    if not allow_llm:
        repair_context["llm_warning"] = "已达到 LLM 调用上限，跳过计划冲突 LLM 修复。"
        return route, backup_plan, repair_context, warning
    if not evaluator.get("needs_llm_review"):
        return route, backup_plan, repair_context, warning

    raw_review, llm_warning = _run_llm_review(
        parsed_goal=parsed_goal,
        evaluator=evaluator,
        weather_context=weather_context,
        sunlight_context=sunlight_context,
        map_context=map_context,
        reference_context=reference_context,
        route=route,
    )
    if llm_warning:
        repair_context["llm_warning"] = llm_warning
        return route, backup_plan, repair_context, llm_warning
    if not raw_review:
        return route, backup_plan, repair_context, "LLM 计划修复未返回可用 JSON，已保留规则方案。"

    review = _sanitize_review(raw_review, route, evaluator)
    repaired_route, repaired_backup = _apply_review(route, backup_plan, review)
    repair_context.update(
        {
            "llm_used": True,
            "llm_review": review,
            "applied": repaired_route != route or repaired_backup != backup_plan,
        }
    )
    if review.get("user_facing_warning"):
        warning = review["user_facing_warning"]
    return repaired_route, repaired_backup, repair_context, warning
