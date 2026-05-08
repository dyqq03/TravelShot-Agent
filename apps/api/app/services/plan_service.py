from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from time import monotonic
from copy import deepcopy
from uuid import uuid4

from app.agent.llm_planner import (
    PlannerLLMError,
    analyze_user_intent,
    answer_followup_with_tools,
    generate_llm_plan,
)
from app.core.config import settings
from app.db.postgres import get_pool
from app.db.repository import (
    cleanup_expired_travel_plans,
    delete_travel_plan,
    get_cached_completed_plan,
    get_travel_plan,
    insert_plan_message,
    insert_travel_plan,
    list_plan_messages,
    list_plan_options,
    list_plan_route,
    list_travel_plans,
    replace_agent_steps,
    replace_plan_route_items,
    replace_spot_time_options,
    search_photo_spots,
    touch_travel_plan,
    try_mark_plan_generating,
    update_plan_execution_state,
    update_plan_status,
    update_travel_plan_result,
)
from app.schemas.plans import ExecutionAdjustRequest, ExecutionStateRequest, FollowUpRequest, PlanCreateRequest


class PlanGenerationBlocked(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


logger = logging.getLogger(__name__)


def _request_hash(user_input: str, reference_images: list[str]) -> str:
    payload = {
        "user_input": " ".join(user_input.split()),
        "reference_images": [hashlib.sha256(image.encode("utf-8")).hexdigest() for image in reference_images],
        "llm_model": settings.llm_model,
        "vision_model": settings.vision_model,
        "agent_max_llm_calls": settings.agent_max_llm_calls,
        "agent_max_tool_rounds": settings.agent_max_tool_rounds,
        "quality_gate_version": 2,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _log_plan_timing(plan_id: str, stage: str, started: float) -> None:
    logger.info("plan_id=%s stage=%s duration_ms=%s", plan_id, stage, round((monotonic() - started) * 1000))


def _blocked_message(exc: PlannerLLMError) -> str:
    detail = str(exc)
    lowered = detail.lower()
    if "unexpected_eof_while_reading" in lowered or "eof occurred in violation of protocol" in lowered:
        return (
            "LLM model call failed: the TLS connection to the model provider was closed unexpectedly after retries. "
            "This is usually a temporary provider/network/proxy interruption, not a prompt or schema bug. "
            f"Original error: {detail}"
        )
    if "temporary network/provider error" in lowered or "timed out" in lowered or "urlerror" in lowered:
        return (
            "LLM model call failed: temporary network/provider error after retries. "
            "Please retry in a moment, or check network/proxy/provider availability if it keeps happening. "
            f"Original error: {detail}"
        )
    return f"LLM model call failed: {detail}. Please check LLM_API_KEY, LLM_BASE_URL, model name, network access, and token limits."


async def create_plan(payload: PlanCreateRequest) -> dict:
    plan_id = str(uuid4())
    request_hash = _request_hash(payload.user_input, payload.reference_images)
    parsed_goal = {"raw_text": payload.user_input}
    plan = {
        "plan_id": plan_id,
        "status": "created",
        "user_input": payload.user_input,
        "reference_images": payload.reference_images,
        "request_hash": request_hash,
        "parsed_goal": parsed_goal,
        "visual_goal": {},
        "weather_context": {},
        "sunlight_context": {},
        "map_context": {},
        "reference_context": {},
        "discovery_context": {},
        "image_analysis": {},
        "repair_context": {},
        "task_plan": [],
        "agent_steps": [],
        "final_markdown": None,
        "route": [],
        "spot_time_options": [],
        "backup_plan": [],
        "warnings": [],
        "llm_used": False,
        "execution_state": None,
    }
    await insert_travel_plan(get_pool(), plan)
    return {
        "plan_id": plan_id,
        "status": "created",
        "parsed_goal": parsed_goal,
        "warnings": [],
        "llm_used": False,
    }


async def generate_plan(plan_id: str) -> dict | None:
    pool = get_pool()
    stored = await get_travel_plan(pool, plan_id)
    if not stored:
        return None
    if stored.get("status") in {"completed", "cannot_satisfy"} and stored.get("final_markdown"):
        return _plan_generate_response(stored)
    if not await try_mark_plan_generating(pool, plan_id):
        latest = await get_travel_plan(pool, plan_id)
        if latest and latest.get("status") == "generating":
            raise PlanGenerationBlocked("这个方案正在生成中，请等当前生成完成后再试。")
        if latest:
            return _plan_generate_response(latest)
        return None

    reference_images = stored.get("reference_images") or []
    request_hash = stored.get("request_hash") or _request_hash(stored["user_input"], reference_images)
    cached = await get_cached_completed_plan(
        pool,
        request_hash=request_hash,
        exclude_plan_id=plan_id,
        ttl_seconds=settings.plan_cache_ttl_seconds,
    )
    if cached:
        warnings = list(dict.fromkeys([*(cached.get("warnings") or []), "已命中最终方案缓存，直接复用上次生成结果。"]))
        plan = {
            **cached,
            "plan_id": plan_id,
            "status": cached.get("status") or "completed",
            "user_input": stored["user_input"],
            "reference_images": reference_images,
            "request_hash": request_hash,
            "warnings": warnings,
            "execution_state": stored.get("execution_state"),
        }
        await update_travel_plan_result(pool, plan)
        await replace_agent_steps(pool, plan_id, plan["agent_steps"])
        await replace_spot_time_options(pool, plan_id, plan["spot_time_options"])
        await replace_plan_route_items(pool, plan_id, plan["route"])
        logger.info("plan_id=%s stage=final_plan_cache_hit source_plan_id=%s", plan_id, cached.get("plan_id"))
        return _plan_generate_response(plan)

    started = monotonic()
    try:
        intent_state = await asyncio.to_thread(
            analyze_user_intent,
            user_input=stored["user_input"],
            reference_images=reference_images,
            max_llm_calls=settings.agent_max_llm_calls,
        )
    except PlannerLLMError as exc:
        await update_plan_status(pool, plan_id, "failed", [str(exc)])
        raise PlanGenerationBlocked(_blocked_message(exc)) from exc
    _log_plan_timing(plan_id, "intent_analysis", started)

    parsed_goal = intent_state["parsed_goal"]
    warnings = list(dict.fromkeys([*(stored.get("warnings") or []), *(intent_state.get("warnings") or [])]))
    await insert_travel_plan(
        pool,
        {
            **stored,
            "status": "generating",
            "parsed_goal": parsed_goal,
            "warnings": warnings,
            "llm_used": True,
            "reference_images": reference_images,
            "request_hash": request_hash,
        },
    )

    started = monotonic()
    reference_spots = await search_photo_spots(pool, parsed_goal)
    _log_plan_timing(plan_id, "reference_spots_repository", started)
    initial_steps = list(intent_state.get("agent_steps") or [])
    initial_steps.append(
        {
            "task_id": "reference_spots_repository",
            "step_type": "tool",
            "reasoning_summary": "Load optional seed photo spots for LLM reference.",
            "tool_name": "photo_spots_repository",
            "tool_input": {"destination": parsed_goal.get("destination")},
            "tool_output": {
                "success": True,
                "source": "postgresql.photo_spots",
                "data": {"count": len(reference_spots)},
                "error": None,
            },
            "observation": {"count": len(reference_spots), "source": "postgresql.photo_spots"},
        }
    )
    started = monotonic()
    try:
        state = await asyncio.to_thread(
            generate_llm_plan,
            plan_id=plan_id,
            user_input=stored["user_input"],
            reference_images=reference_images,
            reference_spots=reference_spots,
            max_llm_calls=settings.agent_max_llm_calls,
            parsed_goal=parsed_goal,
            intent_tool_requests=intent_state.get("tool_requests") or [],
            initial_steps=initial_steps,
            initial_warnings=warnings,
            initial_llm_calls=intent_state.get("llm_calls") or 0,
        )
    except PlannerLLMError as exc:
        await update_plan_status(pool, plan_id, "failed", [str(exc)])
        raise PlanGenerationBlocked(_blocked_message(exc)) from exc
    _log_plan_timing(plan_id, "llm_plan_generation", started)

    plan = {
        "plan_id": plan_id,
        "status": state.get("status") or "completed",
        "user_input": stored["user_input"],
        "parsed_goal": state.get("parsed_goal") or parsed_goal,
        "task_plan": state.get("task_plan") or [],
        "agent_steps": state.get("agent_steps") or [],
        "visual_goal": state.get("visual_goal") or {},
        "weather_context": state.get("weather_context") or {},
        "sunlight_context": state.get("sunlight_context") or {},
        "map_context": state.get("map_context") or {},
        "reference_context": state.get("reference_context") or {},
        "discovery_context": state.get("discovery_context") or {},
        "image_analysis": state.get("image_analysis") or {},
        "repair_context": state.get("repair_context") or {},
        "route": state.get("optimized_route") or [],
        "spot_time_options": state.get("spot_time_options") or [],
        "backup_plan": state.get("backup_plan") or [],
        "final_markdown": state.get("final_markdown") or "",
        "warnings": state.get("warnings") or [],
        "llm_used": True,
        "execution_state": stored.get("execution_state"),
        "reference_images": reference_images,
        "request_hash": request_hash,
    }
    await update_travel_plan_result(pool, plan)
    await replace_agent_steps(pool, plan_id, plan["agent_steps"])
    await replace_spot_time_options(pool, plan_id, plan["spot_time_options"])
    await replace_plan_route_items(pool, plan_id, plan["route"])
    return _plan_generate_response(plan)


async def get_plan(plan_id: str) -> dict | None:
    pool = get_pool()
    plan = await get_travel_plan(pool, plan_id)
    if plan:
        await touch_travel_plan(pool, plan_id)
    return plan


async def list_plans(limit: int = 30) -> list[dict]:
    pool = get_pool()
    await cleanup_expired_travel_plans(pool, settings.history_retention_days)
    return await list_travel_plans(pool, limit=limit)


async def delete_plan(plan_id: str) -> bool:
    return await delete_travel_plan(get_pool(), plan_id)


async def list_messages(plan_id: str) -> list[dict] | None:
    return await list_plan_messages(get_pool(), plan_id)


async def follow_up_plan(plan_id: str, payload: FollowUpRequest) -> dict | None:
    pool = get_pool()
    plan = await get_travel_plan(pool, plan_id)
    if not plan:
        return None
    try:
        result = await asyncio.to_thread(
            answer_followup_with_tools,
            plan=plan,
            question=payload.question,
            reference_images=payload.reference_images,
            max_llm_calls=settings.agent_max_llm_calls,
        )
    except PlannerLLMError as exc:
        raise PlanGenerationBlocked(_blocked_message(exc)) from exc

    await insert_plan_message(
        pool,
        {
            "plan_id": plan_id,
            "role": "user",
            "content": payload.question,
            "reference_images": payload.reference_images,
            "tool_requests": [],
            "tool_results": [],
            "response": {},
            "warnings": [],
        },
    )
    await insert_plan_message(
        pool,
        {
            "plan_id": plan_id,
            "role": "assistant",
            "content": result.get("answer") or "",
            "reference_images": [],
            "tool_requests": result.get("tool_requests") or [],
            "tool_results": result.get("tool_results") or [],
            "response": result.get("response") or {},
            "warnings": result.get("warnings") or [],
        },
    )
    await touch_travel_plan(pool, plan_id)
    messages = await list_plan_messages(pool, plan_id) or []
    return {
        "plan_id": plan_id,
        "status": result.get("status") or "answered",
        "answer": result.get("answer") or "",
        "changes": result.get("changes") or [],
        "tool_results": result.get("tool_results") or [],
        "warnings": result.get("warnings") or [],
        "messages": messages,
    }


async def list_spot_time_options(plan_id: str) -> list[dict] | None:
    return await list_plan_options(get_pool(), plan_id)


async def list_route(plan_id: str) -> list[dict] | None:
    return await list_plan_route(get_pool(), plan_id)


async def start_live_mode(plan_id: str) -> dict | None:
    pool = get_pool()
    plan = await get_travel_plan(pool, plan_id)
    if not plan:
        return None

    route = plan.get("route") or []
    execution_state = {
        "current_time": "not_started",
        "current_location": None,
        "completed_route_items": [],
        "skipped_route_items": [],
        "remaining_route_items": [item.get("item_id") for item in route if item.get("item_id")],
        "minutes_behind_schedule": 0,
        "user_feedback": [],
        "current_weather": plan.get("weather_context"),
        "plan_validity": {"status": "valid", "reason": "Live mode just started."},
        "next_best_action": {
            "type": "go_to_spot",
            "spot": route[0].get("spot_name") if route else "generate_plan_first",
            "depart_now": bool(route),
            "reason": "Follow the generated route first, then adjust from live feedback.",
        },
    }
    await update_plan_execution_state(pool, plan_id, execution_state, status="live")
    return {"plan_id": plan_id, "status": "live", "execution_state": deepcopy(execution_state)}


async def update_execution_state(plan_id: str, payload: ExecutionStateRequest) -> dict | None:
    pool = get_pool()
    plan = await get_travel_plan(pool, plan_id)
    if not plan:
        return None

    if plan.get("execution_state") is None:
        started = await start_live_mode(plan_id)
        if started is None:
            return None
        state = started["execution_state"]
    else:
        state = deepcopy(plan["execution_state"])

    if payload.current_time:
        state["current_time"] = payload.current_time
    if payload.current_location:
        state["current_location"] = payload.current_location
    if payload.user_feedback:
        state.setdefault("user_feedback", []).append(payload.user_feedback)
    await update_plan_execution_state(pool, plan_id, state)
    return deepcopy(state)


async def adjust_plan(plan_id: str, payload: ExecutionAdjustRequest) -> dict | None:
    question = payload.reason
    if payload.current_time:
        question = f"{question}\nCurrent time: {payload.current_time}"
    if payload.current_location:
        question = f"{question}\nCurrent location: {payload.current_location}"
    return await follow_up_plan(plan_id, FollowUpRequest(question=question, reference_images=[]))


def _plan_generate_response(plan: dict) -> dict:
    return {
        "plan_id": plan["plan_id"],
        "status": plan["status"],
        "parsed_goal": deepcopy(plan["parsed_goal"]),
        "visual_goal": deepcopy(plan["visual_goal"] or {}),
        "weather_context": deepcopy(plan["weather_context"] or {}),
        "sunlight_context": deepcopy(plan["sunlight_context"] or {}),
        "map_context": deepcopy(plan.get("map_context") or {}),
        "reference_context": deepcopy(plan.get("reference_context") or {}),
        "discovery_context": deepcopy(plan.get("discovery_context") or {}),
        "image_analysis": deepcopy(plan.get("image_analysis") or {}),
        "repair_context": deepcopy(plan.get("repair_context") or {}),
        "task_plan": deepcopy(plan["task_plan"]),
        "agent_steps": deepcopy(plan["agent_steps"]),
        "final_markdown": plan["final_markdown"] or "",
        "route": deepcopy(plan["route"]),
        "spot_time_options": deepcopy(plan["spot_time_options"]),
        "backup_plan": deepcopy(plan["backup_plan"]),
        "warnings": deepcopy(plan["warnings"]),
        "llm_used": bool(plan.get("llm_used")),
    }
