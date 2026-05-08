from __future__ import annotations

import json
import re
from time import monotonic
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.agent.llm_contracts import ALLOWED_TOOLS, contract_schema, validate_contract
from app.agent.llm import complete_json, complete_json_multimodal, is_llm_configured
from app.core.config import settings
from app.core.date_parser import parse_duration_days, parse_user_date_range
from app.spot.cities import CITY_PROFILES
from app.tools.base import ToolResult, now_iso, tool_result
from app.tools.geocode import nominatim_geocode
from app.tools.map import poi_search, route_options
from app.tools.search import tavily_search
from app.tools.sunlight import build_sunlight_context
from app.tools.weather import fetch_weather_context


TRANSPORT_ESTIMATE_RE = re.compile(
    r"(高德|打车|骑行|步行|公交|地铁|自驾|开车|驾车).{0,18}(\d+\s*(分钟|小时|公里|km)|约\s*\d+)",
    re.IGNORECASE,
)
NUMERIC_TRAVEL_RE = re.compile(r"\d+\s*(分钟|小时|公里|km)", re.IGNORECASE)
ASCII_TRAVEL_RE = re.compile(r"\d+\s*(min|mins|minute|minutes|h|hr|hrs|hour|hours|km|kilometer|kilometers)", re.IGNORECASE)
COMBINED_PLACE_RE = re.compile(r"(?<!G)(?<!S)(?<!国道)(?<!省道)[/／]")
TRANSPORT_WORDS = ("高德", "打车", "骑行", "步行", "公交", "地铁", "自驾", "开车", "驾车", "taxi", "bike", "cycling", "walk", "drive", "driving", "transit")
REPEAT_VISIT_TERMS = (
    "repeat",
    "revisit",
    "same spot",
    "same place",
    "same location",
    "twice",
    "multiple times",
    "day and night",
    "morning and night",
    "sunrise and sunset",
    "\u91cd\u590d",
    "\u591a\u6b21",
    "\u518d\u53bb",
    "\u518d\u62cd",
    "\u53c8\u53bb",
    "\u4e24\u6b21",
    "\u540c\u4e00\u5730",
    "\u540c\u4e00\u4e2a\u5730",
    "\u540c\u4e00\u4e2a\u666f\u70b9",
    "\u65e9\u665a",
    "\u65e5\u51fa\u548c\u65e5\u843d",
    "\u767d\u5929\u548c\u591c\u666f",
)
REPEATABLE_NON_SCENIC_TERMS = (
    "airport",
    "station",
    "hotel",
    "\u673a\u573a",
    "\u706b\u8f66\u7ad9",
    "\u9ad8\u94c1\u7ad9",
    "\u5ba2\u8fd0\u7ad9",
    "\u9152\u5e97",
    "\u6c11\u5bbf",
    "\u5ba2\u6808",
)
SPOT_KEY_SUFFIXES = (
    "\u98ce\u666f\u540d\u80dc\u533a",
    "\u5386\u53f2\u6587\u5316\u8857\u533a",
    "\u6587\u5316\u8857\u533a",
    "\u5386\u53f2\u8857\u533a",
    "\u6b65\u884c\u8857",
    "\u8857\u533a",
    "\u666f\u533a",
    "\u666f\u70b9",
    "\u516c\u56ed",
    "\u5e7f\u573a",
    "\u89c2\u666f\u53f0",
)
SPOT_SUBPLACE_MARKERS = (
    "游客中心",
    "服务中心",
    "售票处",
    "停车场",
    "图书馆",
    "入口",
    "出口",
    "东门",
    "西门",
    "南门",
    "北门",
    "码头",
    "周边",
)


class PlannerLLMError(RuntimeError):
    pass


@dataclass
class PlannerSession:
    max_calls: int
    calls: int = 0
    steps: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def can_call(self) -> bool:
        return self.calls < max(self.max_calls, 1)

    def call_json(
        self,
        *,
        task_id: str,
        system_prompt: str,
        user_prompt: str,
        images: list[str] | None = None,
        contract: str | None = None,
    ) -> dict[str, Any]:
        if not is_llm_configured():
            raise PlannerLLMError("LLM_API_KEY is not configured. Intent parsing and planning require a working LLM.")
        if not self.can_call():
            raise PlannerLLMError(f"LLM call limit reached ({self.max_calls}). The agent stopped to avoid an infinite loop.")

        self.calls += 1
        started_at = now_iso()
        started = monotonic()
        prompt_chars = len(system_prompt) + len(user_prompt)
        if images:
            payload, error = complete_json_multimodal(system_prompt, user_prompt, images)
        else:
            payload, error = complete_json(system_prompt, user_prompt)

        step = {
            "task_id": task_id,
            "step_type": "llm",
            "reasoning_summary": f"LLM call {self.calls}/{self.max_calls}: {task_id}",
            "tool_name": "llm",
            "tool_input": {"task_id": task_id, "has_images": bool(images), "prompt_chars": prompt_chars},
            "tool_output": {
                "success": payload is not None,
                "source": "llm.chat_completions",
                "error": None if payload is not None else error,
                "warning": error if payload is not None else None,
                "started_at": started_at,
                "finished_at": now_iso(),
            },
            "observation": {"keys": sorted(payload.keys()) if isinstance(payload, dict) else []},
            "duration_ms": round((monotonic() - started) * 1000),
        }
        self.steps.append(step)

        if error:
            if payload is not None:
                self.warnings.append(error)
            else:
                raise PlannerLLMError(f"{task_id} failed with {prompt_chars} prompt chars: {error}")
        if not isinstance(payload, dict):
            raise PlannerLLMError("LLM did not return a JSON object.")
        if contract:
            errors = validate_contract(contract, payload)
            step["tool_output"]["schema_valid"] = not errors
            step["observation"]["schema"] = contract
            step["observation"]["schema_errors"] = errors[:8]
            if errors:
                payload = self._repair_contract(
                    task_id=task_id,
                    contract=contract,
                    invalid_payload=payload,
                    errors=errors,
                )
        return payload

    def _repair_contract(
        self,
        *,
        task_id: str,
        contract: str,
        invalid_payload: dict[str, Any],
        errors: list[str],
    ) -> dict[str, Any]:
        if not self.can_call():
            raise PlannerLLMError(
                f"LLM output failed schema validation for {contract}, and call limit ({self.max_calls}) was reached. "
                f"Errors: {'; '.join(errors[:6])}"
            )

        self.calls += 1
        started_at = now_iso()
        started = monotonic()
        system_prompt = (
            "You repair one JSON object so it strictly matches the required schema. "
            "Return JSON only, with no prose or markdown. Keep true facts from the original payload. "
            "Use null for unknown nullable values and [] for unknown arrays. "
            "Do not invent weather, coordinates, routes, dates, opening hours, or tool results."
        )
        user_prompt = json.dumps(
            {
                "contract": contract,
                "required_schema": contract_schema(contract),
                "validation_errors": errors[:20],
                "invalid_payload": invalid_payload,
            },
            ensure_ascii=False,
            default=str,
        )
        payload, error = complete_json(system_prompt, user_prompt)
        repair_step = {
            "task_id": f"{task_id}_schema_repair",
            "step_type": "llm",
            "reasoning_summary": f"LLM call {self.calls}/{self.max_calls}: repair {contract} schema",
            "tool_name": "llm.schema_repair",
            "tool_input": {"task_id": task_id, "contract": contract, "errors": errors[:8]},
            "tool_output": {
                "success": payload is not None,
                "source": "llm.chat_completions",
                "error": None if payload is not None else error,
                "warning": error if payload is not None else None,
                "started_at": started_at,
                "finished_at": now_iso(),
            },
            "observation": {"schema": contract, "keys": sorted(payload.keys()) if isinstance(payload, dict) else []},
            "duration_ms": round((monotonic() - started) * 1000),
        }
        self.steps.append(repair_step)
        if error or not isinstance(payload, dict):
            raise PlannerLLMError(error or f"LLM schema repair for {contract} did not return a JSON object.")

        repaired_errors = validate_contract(contract, payload)
        repair_step["tool_output"]["schema_valid"] = not repaired_errors
        repair_step["observation"]["schema_errors"] = repaired_errors[:8]
        if repaired_errors:
            raise PlannerLLMError(
                f"LLM output failed schema validation for {contract} after repair. "
                f"Errors: {'; '.join(repaired_errors[:8])}"
            )
        return payload


def generate_llm_plan(
    *,
    plan_id: str,
    user_input: str,
    reference_images: list[str],
    reference_spots: list[dict[str, Any]],
    max_llm_calls: int,
    parsed_goal: dict[str, Any] | None = None,
    intent_tool_requests: list[dict[str, Any]] | None = None,
    initial_steps: list[dict[str, Any]] | None = None,
    initial_warnings: list[str] | None = None,
    initial_llm_calls: int = 0,
) -> dict[str, Any]:
    session = PlannerSession(
        max_calls=max_llm_calls,
        calls=max(initial_llm_calls, 0),
        steps=list(initial_steps or []),
        warnings=list(initial_warnings or []),
    )
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()

    if parsed_goal is None:
        intent = session.call_json(
            task_id="intent_analysis",
            system_prompt=_intent_system_prompt(),
            user_prompt=_intent_user_prompt(user_input, today),
            images=reference_images,
            contract="intent_analysis",
        )
        parsed_goal = _normalize_intent(intent, user_input)
        intent_tool_requests = _sanitize_tool_requests(intent.get("tool_requests"))
    initial_requests = _merge_default_requests(
        parsed_goal,
        intent_tool_requests,
        user_input=user_input,
        reference_spot_count=len(reference_spots),
    )
    tool_results = _execute_tool_requests(session, initial_requests)
    draft = _run_agent_tool_loop(
        session=session,
        user_input=user_input,
        parsed_goal=parsed_goal,
        reference_spots=reference_spots,
        tool_results=tool_results,
        today=today,
    )

    final_payload = session.call_json(
        task_id="final_plan_judgement",
        system_prompt=_final_system_prompt(),
        user_prompt=_final_user_prompt(
            user_input=user_input,
            intent=parsed_goal,
            draft=draft,
            tool_results=tool_results,
            reference_spots=reference_spots,
            today=today,
        ),
        contract="final_plan",
    )
    final_payload = _ensure_final_plan_quality(
        session=session,
        user_input=user_input,
        parsed_goal=parsed_goal,
        draft=draft,
        final_payload=final_payload,
        tool_results=tool_results,
        reference_spots=reference_spots,
        today=today,
    )
    final_route_requests, final_route_warnings = _route_requests_from_draft(final_payload, parsed_goal)
    session.warnings.extend(final_route_warnings)
    if final_route_requests:
        tool_results.extend(_execute_tool_requests(session, final_route_requests))

    final_payload = _normalize_final(final_payload, parsed_goal)
    route = _normalize_route(final_payload.get("route") or [])
    route = _attach_transfer_results(route, tool_results)
    route, route_note_warnings = _sanitize_route_notes(route)
    warnings = _unique_strings([*session.warnings, *route_note_warnings, *_tool_warnings(tool_results), *(final_payload.get("warnings") or [])])
    weather_context = _first_tool_data(tool_results, "weather_lookup", "weather_context")
    sunlight_context = _first_tool_data(tool_results, "sunlight_lookup", "sunlight_context")

    status = "completed" if final_payload.get("status") == "completed" else "cannot_satisfy"
    tool_failures = [item for item in tool_results if not (item.get("result") or {}).get("success")]
    if status == "completed":
        markdown = _render_completed_markdown(
            parsed_goal=parsed_goal,
            weather_context=weather_context,
            sunlight_context=sunlight_context,
            route=route,
            backup_plan=final_payload.get("backup_plan") or [],
            warnings=warnings,
            assumptions=final_payload.get("assumptions") or [],
            tool_failures=tool_failures,
        )
    else:
        markdown = final_payload.get("markdown") or _fallback_markdown(final_payload, warnings)
        if "无法满足" not in markdown:
            markdown = f"# 无法完整满足当前需求\n\n{markdown}"
    return {
        "plan_id": plan_id,
        "status": status,
        "parsed_goal": parsed_goal,
        "visual_goal": {
            "primary_goal": final_payload.get("answer_summary") or parsed_goal.get("summary"),
            "reference_image": parsed_goal.get("image_analysis") or {},
        },
        "weather_context": weather_context,
        "sunlight_context": sunlight_context,
        "map_context": _build_map_context(route, tool_results),
        "reference_context": _build_reference_context(reference_spots, tool_results),
        "discovery_context": {
            "tool_results": tool_results,
            "tool_failures": tool_failures,
            "explicit_locations": parsed_goal.get("explicit_locations") or [],
            "llm_call_count": session.calls,
            "llm_call_limit": session.max_calls,
        },
        "image_analysis": parsed_goal.get("image_analysis") or {},
        "repair_context": {
            "evaluation": {
                "status": "invalid" if status == "cannot_satisfy" else "ok",
                "issues": [{"severity": "invalid", "message": item} for item in final_payload.get("unable_to_satisfy") or []],
                "recommended_action": "explain_limits" if status == "cannot_satisfy" else "continue",
                "needs_llm_review": False,
            },
            "llm_used": True,
            "llm_review": {
                "decision": status,
                "user_facing_warning": "; ".join(warnings[:3]) if warnings else None,
                "confidence": final_payload.get("confidence"),
                "evidence_refs": final_payload.get("evidence_refs") or [],
            },
            "applied": status == "completed",
        },
        "task_plan": final_payload.get("task_plan") or _task_plan_from_tools(tool_results),
        "agent_steps": session.steps,
        "final_markdown": markdown,
        "optimized_route": route,
        "spot_time_options": [],
        "backup_plan": final_payload.get("backup_plan") or [],
        "warnings": warnings,
        "llm_used": True,
    }


def analyze_user_intent(
    *,
    user_input: str,
    reference_images: list[str],
    max_llm_calls: int,
) -> dict[str, Any]:
    session = PlannerSession(max_calls=max_llm_calls)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    intent = session.call_json(
        task_id="intent_analysis",
        system_prompt=_intent_system_prompt(),
        user_prompt=_intent_user_prompt(user_input, today),
        images=reference_images,
        contract="intent_analysis",
    )
    parsed_goal = _normalize_intent(intent, user_input)
    return {
        "parsed_goal": parsed_goal,
        "tool_requests": _sanitize_tool_requests(intent.get("tool_requests")),
        "warnings": session.warnings,
        "agent_steps": session.steps,
        "llm_calls": session.calls,
    }


def _run_agent_tool_loop(
    *,
    session: PlannerSession,
    user_input: str,
    parsed_goal: dict[str, Any],
    reference_spots: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    today: str,
) -> dict[str, Any]:
    max_rounds = max(settings.agent_max_tool_rounds, 1)
    draft: dict[str, Any] | None = None
    for round_index in range(1, max_rounds + 1):
        if not session.can_call():
            session.warnings.append(
                f"Agent tool loop stopped before round {round_index}: LLM call budget {session.max_calls} reached."
            )
            break

        draft = session.call_json(
            task_id=f"agent_tool_loop_round_{round_index}",
            system_prompt=_draft_system_prompt(),
            user_prompt=_draft_user_prompt(
                user_input=user_input,
                intent=parsed_goal,
                reference_spots=reference_spots,
                tool_results=tool_results,
                today=today,
                round_index=round_index,
                max_rounds=max_rounds,
            ),
            contract="draft_plan",
        )

        route_requests, route_request_warnings = _route_requests_from_draft(draft, parsed_goal)
        session.warnings.extend(route_request_warnings)
        requested = _sanitize_tool_requests(draft.get("tool_requests")) + route_requests
        requested = _filter_new_tool_requests(requested, tool_results)
        if requested:
            tool_results.extend(_execute_tool_requests(session, requested))
            if round_index == max_rounds:
                session.warnings.append(
                    f"Agent tool loop reached AGENT_MAX_TOOL_ROUNDS={max_rounds}; final answer will use currently available observations."
                )
            continue

        if draft.get("status") == "need_more_tools":
            session.warnings.append("Agent requested more tools but did not provide any new executable tool requests.")
        break

    if draft is not None:
        return draft
    return {
        "status": "cannot_satisfy",
        "reason": "LLM call budget was reached before the planning loop could start.",
        "tool_requests": [],
        "route": [],
        "warnings": ["LLM call budget reached before planning loop."],
        "unable_to_satisfy": ["LLM call budget reached before planning loop."],
    }


def answer_followup_with_tools(
    *,
    plan: dict[str, Any],
    question: str,
    reference_images: list[str],
    max_llm_calls: int,
) -> dict[str, Any]:
    session = PlannerSession(max_calls=max_llm_calls)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    analysis = session.call_json(
        task_id="followup_intent",
        system_prompt=_followup_intent_system_prompt(),
        user_prompt=_followup_intent_user_prompt(plan, question, today),
        images=reference_images,
        contract="followup_intent",
    )
    requests = _sanitize_tool_requests(analysis.get("tool_requests"))
    tool_results = _execute_tool_requests(session, requests)
    response = session.call_json(
        task_id="followup_answer",
        system_prompt=_followup_answer_system_prompt(),
        user_prompt=_followup_answer_user_prompt(plan, question, analysis, tool_results, today),
        contract="followup_answer",
    )
    warnings = _unique_strings([*session.warnings, *_tool_warnings(tool_results), *(response.get("warnings") or [])])
    status = response.get("status") if response.get("status") in {"answered", "cannot_satisfy"} else "answered"
    return {
        "status": status,
        "answer": str(response.get("answer") or ""),
        "changes": response.get("changes") if isinstance(response.get("changes"), list) else [],
        "tool_requests": requests,
        "tool_results": tool_results,
        "warnings": warnings,
        "agent_steps": session.steps,
        "response": response,
    }


def _intent_system_prompt() -> str:
    return (
        """
You are a travel photography planning agent. Extract user intent from text and optional reference images.
Return JSON only, with schema keys exactly as written.
Use Simplified Chinese for user-facing values; keep schema keys in English.
Do not invent destinations, dates, weather, coordinates, opening hours, route times, or attractions.
For nullable scalar fields use null when unknown; for arrays use [] and add a clear item to unknowns.

Required JSON contract:
"""
        + contract_schema("intent_analysis")
        + """

Allowed tools and argument schemas:
- weather_lookup: {"destination": string, "date_range": ["YYYY-MM-DD"], "lat": number|null, "lng": number|null}
- sunlight_lookup: {"destination": string, "date_range": ["YYYY-MM-DD"], "lat": number|null, "lng": number|null}
- tavily_search: {"query": string, "max_results": number}
- nominatim_geocode: {"query": string, "city": string|null, "limit": number}
- amap_poi_search: {"query": string, "city": string|null, "limit": number}
- amap_route_options: {"origin": {"name": string, "lat": number, "lng": number, "city": string|null}, "destination": {"name": string, "lat": number, "lng": number, "city": string|null}, "modes": ["walking"|"bicycling"|"taxi"|"transit"]}

Tool policy for intent analysis:
- Request tools only when they are useful for the next evidence gap; later rounds can request more.
- For broad destination city/region requests with no concrete attractions, request tavily_search for candidate attractions/photo spots.
- Request nominatim_geocode for explicit named places or destination cities when coordinates are needed.
- Request weather_lookup and sunlight_lookup only when date_range and coordinates are known; otherwise request geocoding first.
- Do not request amap_route_options in intent analysis unless both origin and destination coordinates are already known.
- If the user gives only a broad city, do not invent specific attractions here; leave selection to the planning stage.
"""
    ).strip()


def _intent_user_prompt(user_input: str, today: str) -> str:
    return f"""
Current China date: {today}.
User request:
{user_input}

Rules:
- Interpret relative dates against the current China date.
- "next weekend" or "下周末" means Saturday and Sunday of the next calendar weekend, so output two dates.
- Relative dates such as "后天", "大后天", "下周三", "下个月12号" and date ranges such as "5月20号到22号" must be interpreted against current_china_date.
- For multi-day trips, date_range must contain every calendar date, not only the start and end date.
- "一周", "7天", or "七天" means seven consecutive dates when a start date can be inferred.
- If the date is unknown, use date_range=[] and add a concrete unknowns item. Do not default to today.
- If the user explicitly asks for sunrise, sunset, Great Wall, beach, etc., keep those as must_satisfy or explicit_locations.
- For unknown city coordinates, ask for geocoding first; the backend can then call weather_lookup and sunlight_lookup with resolved lat/lng.
- Ask for geocoding tools for explicit place names.
- If the user only names a city and asks for an itinerary, ask for tavily_search instead of inventing attractions.
- If reference images are present, analyze only travel-photography-relevant style: mood, lighting, composition, pose/action, clothing/props, color palette, possible location types, and concrete replication notes. Merge these into shooting_style, visual_elements, constraints or must_satisfy when useful.
""".strip()


def _draft_system_prompt() -> str:
    return f"""
You are the tool-using planning brain for a travel photography agent.
Think in rounds: observe user intent and existing tool results, then either request the next useful tools or produce a final draft.
Return JSON only, compactly.
Never fabricate coordinates, weather, route time, sunrise/sunset, opening hours, or search facts.
Reference spots are optional hints only; do not force them into the route, but use them when they fit the user's city/style.
If a useful place lacks coordinates, request nominatim_geocode or amap_poi_search instead of guessing coordinates.
If the destination/attractions are broad and not evidenced yet, request tavily_search before choosing concrete spots.
If destination coordinates are known, request weather_lookup and sunlight_lookup when dates are known.
If Nominatim/geocoding evidence is missing or weak, request amap_poi_search for the same concrete place.
If route points have coordinates and the route is same-day local, request amap_route_options for a few adjacent legs.
If the draft cannot cover the requested dates/light goals yet, use status need_more_tools or cannot_satisfy; do not pretend it is complete.

Required JSON contract:
{contract_schema("draft_plan")}
""".strip()


def _draft_user_prompt(
    *,
    user_input: str,
    intent: dict[str, Any],
    reference_spots: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    today: str,
    round_index: int,
    max_rounds: int,
) -> str:
    return json.dumps(
        {
            "current_china_date": today,
            "agent_round": round_index,
            "max_tool_rounds": max_rounds,
            "user_input": user_input,
            "intent": intent,
            "reference_spots_optional": _compact_reference_spots(reference_spots),
            "tool_results": _compact_tool_results(tool_results, max_items=10),
            "quality_requirements": _quality_requirements(intent, user_input),
            "available_tools": _available_tool_descriptions(),
            "instructions": [
                "Choose tools flexibly based on missing evidence; do not follow a fixed chain if the current observations make a tool unnecessary.",
                "If required facts are missing, request tools instead of guessing.",
                "Use status need_more_tools when tool evidence is still needed; use status final only when you can produce a complete itinerary and shooting plan.",
                "For a two-day date_range, plan both days unless impossible.",
                "For each usable day, include at least sunrise/morning, afternoon, and sunset/blue-hour or night slots when they match the user's request.",
                "Choose concrete destination-appropriate portrait locations from reference_spots_optional or tavily_search results when suitable; seed spots are hints, not mandatory.",
                "If reference_spots_optional is sparse and tavily_search results are present, ground attraction choices in those search results; do not rely on memorized city lists.",
                "For newly discovered attractions without coordinates, request nominatim_geocode for the chosen concrete names; if that fails or looks wrong, request amap_poi_search.",
                "After destination coordinates are known, request weather_lookup and sunlight_lookup with lat/lng when dates are known.",
                "If intent.image_analysis is non-empty, adapt attraction choice, timing, composition, poses, colors, clothing/props and route-item guide details to that reference style.",
                "If sunrise is requested, route should include a sunrise slot near the actual sunrise time.",
                "If sunset is requested, route should include a sunset or evening golden-hour slot near the actual sunset time.",
                "If the user explicitly requested a place, do not omit it unless tools show it is impossible or unsafe; explain why.",
                "Each route item must name one concrete shooting spot; do not combine alternatives with / or 或.",
                "For short same-city trips, do not repeat the same spot_name across dates unless the user explicitly asks to revisit it; subareas such as visitor centers, gates, libraries, or nearby streets count as the same parent spot.",
                "Traffic duration and distance must come from tool results only. Do not write minutes, km, taxi, cycling, walking, Amap/Gaode estimates in route_note.",
                "For broad regional or 4+ day trips, plan by day/area and do not request amap_route_options for every leg; route_note may describe non-numeric transfer assumptions only.",
                "For short same-city trips only, include route items with verified or null coordinates and ask for amap_route_options between a small number of adjacent same-day items.",
                "When selecting a reference spot, copy its exact name, lat and lng from reference_spots_optional.",
                "For guide objects, use concise keys: subject_position, photographer_position, composition, poses, lens, safety_notes.",
            ],
        },
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )


def _final_system_prompt() -> str:
    return f"""
You are the final judge for a travel photography agent. Use only user intent, optional reference spots, and tool results.
Return JSON only, compactly. Do not make up facts to satisfy the user.
If weather, coordinates, routing, or search tools failed, disclose that and use cautious fallback wording.
Keep the JSON compact. The backend will render the long final markdown from your structured route.
Set markdown to null unless status is cannot_satisfy. Do not put the full itinerary inside markdown.
For completed plans, route is the source of truth. Every route item must be concrete and actionable.
Each route item must name exactly one shooting spot, not slash-separated alternatives.
For short same-city trips, avoid repeated parent spots across route items unless the user explicitly asked for repeated visits; subareas such as visitor centers, gates, libraries, or nearby streets count as the same parent spot.
When the city is not well covered by provided reference spots, prefer attractions supported by tavily_search or POI tool results over memorized assumptions.
Only backend tool results may provide traffic duration/distance. Do not put transport estimates in route_note.
Guide objects must use these concise keys: subject_position, photographer_position, composition, poses, lens, safety_notes.
Keep each guide value to one short sentence, or poses as at most 3 short strings.

Required JSON contract:
{contract_schema("final_plan")}
""".strip()


def _final_user_prompt(
    *,
    user_input: str,
    intent: dict[str, Any],
    draft: dict[str, Any],
    tool_results: list[dict[str, Any]],
    reference_spots: list[dict[str, Any]],
    today: str,
) -> str:
    return json.dumps(
        {
            "current_china_date": today,
            "user_input": user_input,
            "intent": intent,
            "draft": draft,
            "tool_results": _compact_tool_results(tool_results, max_items=10),
            "reference_spots_optional": _compact_reference_spots(reference_spots),
            "quality_requirements": _quality_requirements(intent, user_input),
            "final_rules": [
                "Use all dates in date_range unless unable_to_satisfy explains why.",
                "A completed answer must be a complete itinerary and shooting plan, not a brief suggestion.",
                "For short trips, include at least three route items per date unless status is cannot_satisfy.",
                "For broad regional or 4+ day trips, include one morning/landscape item and one sunset/night or experience item per date, with route_note explaining area transfer assumptions.",
                "For 1-3 day regional trips such as a weekend in Yunnan, choose one compact base area and avoid repeating the same parent scenic spots across days unless the user explicitly requests revisits.",
                "Respect explicit must_satisfy items such as sunrise, sunset, or named places.",
                "Do not combine unrelated places in one spot_name with / or 或; choose one concrete place.",
                "For short same-city trips, do not reuse the same parent spot across different dates unless the user explicitly asked for repeat visits; subareas count as the same parent spot.",
                "Do not put traffic estimates such as taxi/cycling/walking minutes or kilometers into route_note; transfer time is rendered from tool results.",
                "If both sunrise and sunset are requested, route must contain both.",
                "Every route item needs actionable portrait guidance: subject position, photographer position, lens/equipment, poses/actions, and safety/crowd notes.",
                "If intent.image_analysis is non-empty, reflect the reference-image style in route choices and guide details instead of treating the images as generic decoration.",
                "If rain/cloud cover makes a sunny result impossible, say so honestly and offer a rainy/overcast alternative.",
                "Do not write a long markdown field. Set markdown to null or a short note; backend renders the complete user-facing itinerary.",
                "When using a reference spot, copy its exact name, lat and lng from reference_spots_optional.",
                "Mention tool failures and fallback assumptions in warnings.",
            ],
        },
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )


def _followup_intent_system_prompt() -> str:
    return f"""
You adjust an existing travel photography plan. Decide what tools are needed for the follow-up.
Return JSON only with this contract:
{contract_schema("followup_intent")}
Use tools for new weather/date/location/route/search questions. Do not invent facts.
If the user asks only for wording, style, sequence, or a preference change that can be answered from the current plan, use tool_requests=[].
If uploaded images are present, summarize only visual intent relevant to the requested adjustment.
Only request amap_route_options when you already have both origin and destination as objects with name, lat and lng. Otherwise ask for geocoding/search first or use tool_requests=[].
""".strip()


def _followup_intent_user_prompt(plan: dict[str, Any], question: str, today: str) -> str:
    return json.dumps(
        {
            "current_china_date": today,
            "question": question,
            "current_plan": _compact_plan_for_followup(plan),
        },
        ensure_ascii=False,
        default=str,
    )


def _followup_answer_system_prompt() -> str:
    return f"""
You answer a follow-up about an existing travel photography plan.
Return JSON only with this contract:
{contract_schema("followup_answer")}
Only output the needed changes, not the full plan. Be honest when the request cannot be satisfied.
Keep answer under 600 Chinese characters unless status is cannot_satisfy.
Each changes item should name the affected section/time slot and the concrete edit.
Do not claim a tool result that is not present in tool_results.
""".strip()


def _followup_answer_user_prompt(
    plan: dict[str, Any],
    question: str,
    analysis: dict[str, Any],
    tool_results: list[dict[str, Any]],
    today: str,
) -> str:
    return json.dumps(
        {
            "current_china_date": today,
            "question": question,
            "analysis": analysis,
            "tool_results": _compact_tool_results(tool_results, max_items=8),
            "current_plan": _compact_plan_for_followup(plan),
        },
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )


def _normalize_intent(intent: dict[str, Any], user_input: str) -> dict[str, Any]:
    raw_date_range = _string_list(intent.get("date_range"))
    duration_days = _int_or_none(intent.get("duration_days")) or _duration_from_text(user_input)
    date_range = _expand_date_range(raw_date_range, duration_days)
    relative_date_range = _relative_date_range_from_text(user_input)
    if relative_date_range:
        date_range = relative_date_range
        if duration_days is None:
            duration_days = len(relative_date_range)
    explicit_locations = intent.get("explicit_locations") if isinstance(intent.get("explicit_locations"), list) else []
    return {
        "destination": _string_or_none(intent.get("destination")),
        "departure_city": _string_or_none(intent.get("departure_city")),
        "date_range": date_range,
        "duration_days": duration_days or len(date_range) or None,
        "shooting_style": _string_list(intent.get("shooting_style")),
        "visual_elements": _string_list(intent.get("visual_elements")),
        "subject": _string_list(intent.get("subject")),
        "equipment": _string_list(intent.get("equipment")),
        "explicit_locations": [item for item in explicit_locations if isinstance(item, dict)],
        "must_satisfy": _string_list(intent.get("must_satisfy")),
        "constraints": _string_list(intent.get("constraints")),
        "unknowns": _string_list(intent.get("unknowns")),
        "image_analysis": intent.get("image_analysis") if isinstance(intent.get("image_analysis"), dict) else {},
        "summary": _string_or_none(intent.get("summary")),
        "raw_text": user_input,
    }


def _ensure_final_plan_quality(
    *,
    session: PlannerSession,
    user_input: str,
    parsed_goal: dict[str, Any],
    draft: dict[str, Any],
    final_payload: dict[str, Any],
    tool_results: list[dict[str, Any]],
    reference_spots: list[dict[str, Any]],
    today: str,
) -> dict[str, Any]:
    errors = _plan_quality_errors(final_payload, parsed_goal, user_input)
    if not errors:
        return final_payload

    if not session.can_call():
        return _quality_gate_failure_payload(final_payload, errors)

    repaired = session.call_json(
        task_id="final_plan_quality_repair",
        system_prompt=_final_system_prompt(),
        user_prompt=json.dumps(
            {
                "current_china_date": today,
                "user_input": user_input,
                "intent": parsed_goal,
                "draft": draft,
                "previous_final_payload": final_payload,
                "quality_errors_to_fix": errors,
                "tool_results": _compact_tool_results(tool_results, max_items=8),
                "reference_spots_optional": _compact_reference_spots(reference_spots),
                "quality_requirements": _quality_requirements(parsed_goal, user_input),
                "repair_rules": [
                    "Rewrite the final plan so every quality error is fixed.",
                    "Keep JSON compact. Set markdown to null or a note under 300 characters.",
                    "Do not return status completed unless all requested dates are planned.",
                    "Do not return status completed unless requested sunrise and sunset are both represented.",
                    "Use concrete scenic portrait locations. Prefer reference_spots_optional when suitable.",
                    "Do not combine alternatives with / or 或 in spot_name; choose one concrete spot.",
                    "For short 1-3 day trips, including regional/province requests, replace repeated parent spots with different nearby concrete shooting spots unless repeat visits were explicitly requested.",
                    "Remove traffic-time estimates from route_note. Only backend tools provide transfer duration/distance.",
                    "Use null for unknown coordinates; never invent coordinates.",
                    "Use guide keys: subject_position, photographer_position, composition, poses, lens, safety_notes.",
                    "If the evidence truly cannot support a complete plan, return status cannot_satisfy and explain why.",
                ],
            },
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        ),
        contract="final_plan",
    )
    repaired_errors = _plan_quality_errors(repaired, parsed_goal, user_input)
    if repaired_errors:
        return _quality_gate_failure_payload(repaired, repaired_errors)
    session.warnings.append("LLM final plan failed itinerary quality checks once and was rewritten.")
    return repaired


def _quality_gate_failure_payload(payload: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    previous_warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    return {
        **payload,
        "status": "cannot_satisfy",
        "answer_summary": "The model did not produce a complete itinerary that passed quality checks.",
        "markdown": (
            "# 无法生成可靠的完整方案\n\n"
            "模型返回的方案没有通过完整性审查，因此没有把它当作可执行行程展示。\n\n"
            "未通过的检查：\n"
            + "\n".join(f"- {error}" for error in errors[:10])
            + "\n\n可以提高 `AGENT_MAX_LLM_CALLS` 后重试，或缩小需求范围。"
        ),
        "route": [],
        "warnings": [*previous_warnings, *errors],
        "unable_to_satisfy": errors,
    }


def _available_tool_descriptions() -> list[dict[str, Any]]:
    return [
        {
            "tool": "tavily_search",
            "use_when": "Need public/current travel context, attraction lists, photo spots, opening notes, restrictions, or broad destination discovery.",
            "arguments": {"query": "string", "max_results": "number optional"},
        },
        {
            "tool": "nominatim_geocode",
            "use_when": "Need coordinates for a destination city or a concrete attraction before weather, sunlight, or routing.",
            "arguments": {"query": "place name", "city": "string|null", "limit": "number optional"},
        },
        {
            "tool": "amap_poi_search",
            "use_when": "Nominatim failed, returned weak/wrong coordinates, or China POI lookup is needed for a concrete place.",
            "arguments": {"query": "place name", "city": "string|null", "limit": "number optional"},
        },
        {
            "tool": "weather_lookup",
            "use_when": "Destination coordinates and dates are known; checks Open-Meteo forecast.",
            "arguments": {"destination": "string", "date_range": ["YYYY-MM-DD"], "lat": "number|null", "lng": "number|null"},
        },
        {
            "tool": "sunlight_lookup",
            "use_when": "Destination coordinates and dates are known; calculates sunrise, sunset, golden hour and blue hour with Astral.",
            "arguments": {"destination": "string", "date_range": ["YYYY-MM-DD"], "lat": "number|null", "lng": "number|null"},
        },
        {
            "tool": "amap_route_options",
            "use_when": "Adjacent same-day route points have coordinates and local transfer time is needed.",
            "arguments": {
                "origin": {"name": "string", "lat": "number", "lng": "number", "city": "string|null"},
                "destination": {"name": "string", "lat": "number", "lng": "number", "city": "string|null"},
                "modes": ["walking", "bicycling", "taxi", "transit"],
            },
        },
    ]


def _plan_quality_errors(payload: dict[str, Any], parsed_goal: dict[str, Any], user_input: str) -> list[str]:
    if payload.get("status") == "cannot_satisfy":
        unable = payload.get("unable_to_satisfy")
        if isinstance(unable, list) and unable:
            return []
        return ["cannot_satisfy plans must explain unable_to_satisfy reasons."]

    errors: list[str] = []
    route = payload.get("route") if isinstance(payload.get("route"), list) else []
    dates = _string_list(parsed_goal.get("date_range"))
    if not route:
        return ["completed plans must include a route array with concrete itinerary items."]

    if dates:
        planned_dates = {item.get("date") for item in route if isinstance(item, dict)}
        missing_dates = [day for day in dates if day not in planned_dates]
        if missing_dates:
            errors.append(f"route must include every requested date; missing {', '.join(missing_dates)}.")
        min_items = _minimum_items_per_day(parsed_goal, user_input)
        for day in dates:
            count = sum(1 for item in route if isinstance(item, dict) and item.get("date") == day)
            if count < min_items:
                errors.append(f"date {day} needs at least {min_items} route items for a usable itinerary; got {count}.")

    distinct_spots = {
        str(item.get("spot_name") or "").strip()
        for item in route
        if isinstance(item, dict) and str(item.get("spot_name") or "").strip()
    }
    minimum_total = max(len(dates) * _minimum_items_per_day(parsed_goal, user_input), 3 if dates else 3)
    if len(route) < minimum_total:
        errors.append(f"completed itinerary is too short; expected at least {minimum_total} route items, got {len(route)}.")
    if len(distinct_spots) < min(3, minimum_total):
        errors.append("completed itinerary must choose multiple concrete scenic spots, not only one location.")
    if _should_reject_repeated_spots(parsed_goal, user_input):
        errors.extend(_repeated_spot_errors(route))

    route_text = _payload_text({"route": route, "markdown": payload.get("markdown"), "summary": payload.get("answer_summary")})
    if _wants_sunrise(parsed_goal, user_input) and not _contains_any(route_text, ["日出", "晨光", "清晨", "黎明", "sunrise"]):
        errors.append("user requested sunrise, but route/markdown does not include a sunrise or early-morning slot.")
    if _wants_sunset(parsed_goal, user_input) and not _contains_any(route_text, ["日落", "夕阳", "傍晚", "黄昏", "晚霞", "蓝调", "sunset"]):
        errors.append("user requested sunset, but route/markdown does not include a sunset/evening slot.")

    for index, item in enumerate(route):
        if not isinstance(item, dict):
            continue
        spot_name = str(item.get("spot_name") or "").strip()
        if _looks_like_combined_place(spot_name):
            errors.append(f"route[{index}].spot_name combines multiple places; choose one concrete shooting spot.")
        route_note = str(item.get("route_note") or "").strip()
        if route_note and _contains_transport_estimate(route_note):
            errors.append(f"route[{index}].route_note contains transport time/distance estimates; leave transfer timing to tools.")
        guide = item.get("guide")
        if not isinstance(guide, dict) or not guide:
            errors.append(f"route[{index}] must include actionable portrait shooting guide.")
            continue
        guide_text = _payload_text(guide)
        if not _contains_any(guide_text, ["人物", "站位", "摄影", "机位", "构图", "pose", "动作", "镜头", "焦段", "安全", "人流"]):
            errors.append(f"route[{index}].guide is too vague; include position, composition, pose/action, lens/equipment and safety notes.")

    return errors[:20]


def _quality_requirements(parsed_goal: dict[str, Any], user_input: str) -> dict[str, Any]:
    dates = _string_list(parsed_goal.get("date_range"))
    min_items = _minimum_items_per_day(parsed_goal, user_input)
    long_trip = _is_long_or_regional_trip(parsed_goal, user_input)
    reject_repeats = _should_reject_repeated_spots(parsed_goal, user_input)
    return {
        "planning_mode": "regional_overview" if long_trip else "same_city_detail",
        "expected_dates": dates,
        "minimum_route_items_per_date": min_items,
        "minimum_total_route_items": max(len(dates) * min_items, 3 if dates else 3),
        "allow_repeated_spots": not reject_repeats,
        "short_trip_unique_spots_required": reject_repeats,
        "route_tool_policy": (
            "For regional_overview, do not request precise route tools for every leg; use route_note for intercity or scenic-road transfers."
            if long_trip
            else "For same_city_detail, route tools may be used for a few adjacent same-day transfers."
        ),
        "must_include_sunrise_slot": _wants_sunrise(parsed_goal, user_input),
        "must_include_sunset_slot": _wants_sunset(parsed_goal, user_input),
        "must_choose_concrete_scenic_spots": True,
        "route_item_guide_must_include": [
            "subject/person position",
            "photographer/camera position",
            "composition",
            "poses/actions",
            "lens/equipment",
            "crowd/safety/weather note",
        ],
        "completed_status_rule": "Use completed only when all quality requirements are satisfied; otherwise use cannot_satisfy with reasons.",
    }


def _should_reject_repeated_spots(parsed_goal: dict[str, Any], user_input: str) -> bool:
    if _allows_repeated_spots(parsed_goal, user_input):
        return False
    dates = _string_list(parsed_goal.get("date_range"))
    duration = _int_or_none(parsed_goal.get("duration_days")) or len(dates)
    return bool(duration and duration <= 3) or bool(dates and len(dates) <= 3)


def _allows_repeated_spots(parsed_goal: dict[str, Any], user_input: str) -> bool:
    text = _payload_text({"goal": parsed_goal, "user_input": user_input})
    return _contains_any(text, list(REPEAT_VISIT_TERMS))


def _repeated_spot_errors(route: list[Any]) -> list[str]:
    seen: dict[str, tuple[int, str, str | None]] = {}
    errors: list[str] = []
    for index, item in enumerate(route):
        if not isinstance(item, dict):
            continue
        spot_name = str(item.get("spot_name") or "").strip()
        key = _spot_key(spot_name)
        if not key or _is_repeatable_non_scenic_spot(spot_name):
            continue
        date_value = _string_or_none(item.get("date"))
        previous = seen.get(key)
        if previous is None:
            seen[key] = (index, spot_name, date_value)
            continue
        previous_index, previous_name, previous_date = previous
        errors.append(
            f'route repeats spot "{spot_name}" at route[{previous_index}]'
            f" ({previous_date or 'unknown date'}) and route[{index}] ({date_value or 'unknown date'}); "
            "short 1-3 day trips need different concrete shooting spots unless the user requested repeat visits."
        )
    return errors[:8]


def _spot_key(spot_name: str) -> str:
    key = _compact_spot_text(spot_name)
    for delimiter in ("-", "－", "—", "–", "·", "：", ":", "（", "("):
        if delimiter in spot_name:
            key = _compact_spot_text(spot_name.split(delimiter, 1)[0])
            break
    for marker in SPOT_SUBPLACE_MARKERS:
        marker_key = _compact_spot_text(marker)
        marker_index = key.find(marker_key)
        if marker_index >= 2:
            key = key[:marker_index]
            break
    for suffix in SPOT_KEY_SUFFIXES:
        suffix_key = _compact_spot_text(suffix)
        if key.endswith(suffix_key) and len(key) > len(suffix_key) + 1:
            key = key[: -len(suffix_key)]
            break
    return key


def _compact_spot_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.lower(), flags=re.UNICODE)


def _is_repeatable_non_scenic_spot(spot_name: str) -> bool:
    lowered = spot_name.lower()
    return any(term.lower() in lowered for term in REPEATABLE_NON_SCENIC_TERMS)


def _minimum_items_per_day(parsed_goal: dict[str, Any], user_input: str) -> int:
    if _is_long_or_regional_trip(parsed_goal, user_input):
        return 2
    text = _payload_text({"goal": parsed_goal, "user_input": user_input})
    if _contains_any(text, ["行程", "安排", "路线", "写真", "拍摄方案", "itinerary", "route"]):
        return 3
    return 2


def _is_long_or_regional_trip(parsed_goal: dict[str, Any], user_input: str) -> bool:
    dates = _string_list(parsed_goal.get("date_range"))
    duration = _int_or_none(parsed_goal.get("duration_days")) or len(dates)
    destination = str(parsed_goal.get("destination") or "")
    text = _payload_text({"goal": parsed_goal, "user_input": user_input})
    broad_terms = ["新疆", "北疆", "南疆", "西藏", "川西", "青甘", "甘南", "云南", "内蒙古", "环线", "自驾"]
    return duration >= 4 or len(dates) >= 4 or _contains_any(text, ["一周", "7天", "七天"]) or any(term in destination for term in broad_terms)


def _expand_date_range(values: list[str], duration_days: int | None = None) -> list[str]:
    parsed = [_parse_iso_date(value) for value in values]
    valid_dates = [item for item in parsed if item is not None]
    if not valid_dates:
        return values

    duration = duration_days if duration_days and duration_days > 0 else None
    if len(valid_dates) == 1 and duration and 1 < duration <= 31:
        start = valid_dates[0]
        return [(start + timedelta(days=offset)).isoformat() for offset in range(duration)]

    if len(valid_dates) >= 2:
        start, end = valid_dates[0], valid_dates[-1]
        days = (end - start).days + 1
        if 1 < days <= 31 and (duration is None or duration > len(values) or days == duration):
            return [(start + timedelta(days=offset)).isoformat() for offset in range(days)]

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _relative_date_range_from_text(text: str, today: date | None = None) -> list[str]:
    return parse_user_date_range(text, today=today)


def _weekend_relative_date_signal(text: str) -> bool:
    compact = text.replace(" ", "").lower()
    return _contains_any(compact, ["周末", "星期末", "weekend"])


def _duration_from_text(text: str) -> int | None:
    return parse_duration_days(text)


def _wants_sunrise(parsed_goal: dict[str, Any], user_input: str) -> bool:
    return _contains_any(_payload_text({"goal": parsed_goal, "user_input": user_input}), ["日出", "晨光", "清晨", "黎明", "sunrise"])


def _wants_sunset(parsed_goal: dict[str, Any], user_input: str) -> bool:
    return _contains_any(_payload_text({"goal": parsed_goal, "user_input": user_input}), ["日落", "夕阳", "傍晚", "黄昏", "晚霞", "sunset"])


def _payload_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str).lower()


def _contains_any(text: str, needles: list[str]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def _normalize_final(payload: dict[str, Any], parsed_goal: dict[str, Any]) -> dict[str, Any]:
    status = payload.get("status")
    if status not in {"completed", "cannot_satisfy", "final"}:
        status = "completed" if payload.get("route") else "cannot_satisfy"
    if status == "final":
        status = "completed"
    normalized = dict(payload)
    normalized["status"] = status
    normalized.setdefault("route", [])
    normalized.setdefault("warnings", [])
    if parsed_goal.get("date_range") and status == "completed":
        planned_dates = {item.get("date") for item in normalized.get("route") or [] if isinstance(item, dict)}
        missing = [day for day in parsed_goal["date_range"] if day not in planned_dates]
        if missing:
            normalized.setdefault("warnings", []).append(f"LLM route omitted date(s): {', '.join(missing)}. Please verify the plan.")
    return normalized


def _merge_default_requests(
    parsed_goal: dict[str, Any],
    llm_requests: Any,
    *,
    user_input: str = "",
    reference_spot_count: int = 0,
) -> list[dict[str, Any]]:
    requests = _sanitize_tool_requests(llm_requests)
    destination = parsed_goal.get("destination")
    if destination not in CITY_PROFILES:
        requests = [
            request
            for request in requests
            if request.get("tool") not in {"weather_lookup", "sunlight_lookup"}
            or _tool_args_have_coordinates(request.get("arguments"))
        ]
    return _dedupe_tool_requests(requests)


def _tool_args_have_coordinates(arguments: Any) -> bool:
    if not isinstance(arguments, dict):
        return False
    return _float_or_none(arguments.get("lat") or arguments.get("latitude")) is not None and _float_or_none(arguments.get("lng") or arguments.get("longitude")) is not None


def _sanitize_tool_requests(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    requests: list[dict[str, Any]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        tool = item.get("tool")
        args = item.get("arguments")
        if tool not in ALLOWED_TOOLS or not isinstance(args, dict):
            continue
        normalized_args = _sanitize_tool_arguments(str(tool), args)
        if normalized_args is None:
            continue
        requests.append({"tool": tool, "arguments": normalized_args, "reason": str(item.get("reason") or "")})
    return requests


def _sanitize_tool_arguments(tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    if tool in {"weather_lookup", "sunlight_lookup"}:
        destination = _string_or_none(args.get("destination"))
        date_range = _string_list(args.get("date_range"))
        if not destination or not date_range:
            return None
        return {
            "destination": destination,
            "date_range": date_range,
            "lat": _float_or_none(args.get("lat") or args.get("latitude")),
            "lng": _float_or_none(args.get("lng") or args.get("longitude")),
            "coordinate_source": args.get("coordinate_source"),
        }
    if tool == "tavily_search":
        query = _string_or_none(args.get("query"))
        if not query:
            return None
        return {"query": query, "max_results": _int_or_none(args.get("max_results")) or 5}
    if tool in {"nominatim_geocode", "amap_poi_search"}:
        query = _string_or_none(args.get("query"))
        if not query:
            return None
        return {
            "query": query,
            "city": _string_or_none(args.get("city")),
            "limit": _int_or_none(args.get("limit")) or 5,
        }
    if tool == "amap_route_options":
        origin = args.get("origin") if isinstance(args.get("origin"), dict) else None
        destination = args.get("destination") if isinstance(args.get("destination"), dict) else None
        if not origin or not destination or not _point_has_coords(origin) or not _point_has_coords(destination):
            return None
        modes = [mode for mode in _string_list(args.get("modes")) if mode in {"walking", "bicycling", "taxi", "transit"}]
        return {
            "origin": _route_point(origin),
            "destination": _route_point(destination),
            "modes": modes or ["walking", "bicycling", "taxi", "transit"],
        }
    return args


def _dedupe_tool_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for request in requests:
        key = json.dumps({"tool": request.get("tool"), "arguments": request.get("arguments")}, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        result.append(request)
    return result[:24]


def _filter_new_tool_requests(requests: list[dict[str, Any]], tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    executed = {
        json.dumps(
            {"tool": (entry.get("request") or {}).get("tool"), "arguments": (entry.get("request") or {}).get("arguments")},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        for entry in tool_results
    }
    fresh = []
    for request in _dedupe_tool_requests(requests):
        key = json.dumps({"tool": request.get("tool"), "arguments": request.get("arguments")}, ensure_ascii=False, sort_keys=True, default=str)
        if key in executed:
            continue
        fresh.append(request)
    return fresh


def _execute_tool_requests(session: PlannerSession, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    deduped = _dedupe_tool_requests(requests)
    capped, skipped_count = _cap_tool_requests(deduped)
    if skipped_count:
        session.warnings.append(f"Skipped {skipped_count} low-priority tool request(s) to keep generation responsive.")
        session.steps.append(
            {
                "task_id": f"tool_{len(session.steps) + 1}_skipped",
                "step_type": "tool_policy",
                "reasoning_summary": "Limit tool fan-out for broad or long-running planning requests.",
                "tool_name": None,
                "tool_input": {"requested": len(deduped), "executed": len(capped)},
                "tool_output": {"success": True, "skipped_count": skipped_count},
                "observation": {"requested": len(deduped), "executed": len(capped)},
            }
        )
    for index, request in enumerate(capped, start=1):
        started = monotonic()
        result = _execute_tool_request(request)
        duration_ms = round((monotonic() - started) * 1000)
        entry = {
            "request": request,
            "result": result,
            "warning": _tool_warning(request, result),
            "duration_ms": duration_ms,
        }
        results.append(entry)
        session.steps.append(
            {
                "task_id": f"tool_{len(session.steps) + 1}",
                "step_type": "tool",
                "reasoning_summary": request.get("reason") or f"Execute {request.get('tool')}",
                "tool_name": request.get("tool"),
                "tool_input": request.get("arguments") or {},
                "tool_output": result,
                "observation": _tool_observation(result),
                "duration_ms": duration_ms,
            }
        )
        if entry["warning"]:
            session.warnings.append(entry["warning"])
    return results


def _cap_tool_requests(requests: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    max_total = max(settings.agent_max_tool_requests_per_batch, 1)
    max_route = max(settings.agent_max_route_requests, 0)
    max_per_tool = {
        "weather_lookup": 1,
        "sunlight_lookup": 1,
        "tavily_search": 2,
        "nominatim_geocode": 6,
        "amap_poi_search": 4,
        "amap_route_options": max_route,
    }
    priority = {
        "weather_lookup": 0,
        "sunlight_lookup": 1,
        "tavily_search": 2,
        "nominatim_geocode": 3,
        "amap_poi_search": 4,
        "amap_route_options": 5,
    }
    ordered = sorted(enumerate(requests), key=lambda item: (priority.get(str(item[1].get("tool")), 9), item[0]))
    counts: dict[str, int] = {}
    kept: list[tuple[int, dict[str, Any]]] = []
    skipped = 0
    for original_index, request in ordered:
        tool = str(request.get("tool") or "")
        count = counts.get(tool, 0)
        if count >= max_per_tool.get(tool, max_total) or len(kept) >= max_total:
            skipped += 1
            continue
        counts[tool] = count + 1
        kept.append((original_index, request))
    kept.sort(key=lambda item: item[0])
    return [request for _, request in kept], skipped


def _execute_tool_request(request: dict[str, Any]) -> ToolResult:
    tool = request.get("tool")
    args = request.get("arguments") or {}
    try:
        if tool == "weather_lookup":
            destination = str(args.get("destination") or "")
            date_range = _string_list(args.get("date_range"))
            lat = _float_or_none(args.get("lat") or args.get("latitude"))
            lng = _float_or_none(args.get("lng") or args.get("longitude"))
            daily = []
            for day in date_range[:5]:
                daily.append(
                    fetch_weather_context(
                        {
                            "destination": destination,
                            "date_range": [day],
                            "lat": lat,
                            "lng": lng,
                            "coordinate_source": args.get("coordinate_source"),
                        }
                    )
                )
            success = bool(daily) and all(item.get("status") == "live" for item in daily)
            return tool_result(
                success=success,
                source="open_meteo.weather",
                error=None if success else "; ".join(str(item.get("error")) for item in daily if item.get("error")) or "Weather lookup used fallback.",
                data={"weather_context": daily[0] if daily else {}, "daily": daily},
            )
        if tool == "sunlight_lookup":
            destination = str(args.get("destination") or "")
            date_range = _string_list(args.get("date_range"))
            lat = _float_or_none(args.get("lat") or args.get("latitude"))
            lng = _float_or_none(args.get("lng") or args.get("longitude"))
            context = build_sunlight_context(
                {
                    "destination": destination,
                    "date_range": date_range,
                    "lat": lat,
                    "lng": lng,
                    "coordinate_source": args.get("coordinate_source"),
                }
            )
            return tool_result(
                success=context.get("status") == "calculated",
                source="astral.sunlight",
                error=context.get("error"),
                data={"sunlight_context": context},
            )
        if tool == "tavily_search":
            return tavily_search(str(args.get("query") or ""), max_results=_int_or_none(args.get("max_results")))
        if tool == "nominatim_geocode":
            result = nominatim_geocode(
                str(args.get("query") or ""),
                city=_string_or_none(args.get("city")),
                limit=_int_or_none(args.get("limit")) or 3,
            )
            results = (result.get("data") or {}).get("results") or []
            if result.get("success") and results:
                return result
            fallback = poi_search(str(args.get("query") or ""), city=_string_or_none(args.get("city")), limit=_int_or_none(args.get("limit")) or 5)
            data = dict(result.get("data") or {})
            data["nominatim_error"] = result.get("error")
            data["amap_fallback"] = fallback
            pois = (fallback.get("data") or {}).get("pois") or []
            if fallback.get("success") and pois:
                data["results"] = [
                    {
                        "name": poi.get("name"),
                        "display_name": poi.get("address"),
                        "lat": poi.get("lat"),
                        "lng": poi.get("lng"),
                        "source": "amap.poi_search",
                    }
                    for poi in pois
                ]
                return tool_result(success=True, source="nominatim.geocode+amap.fallback", data=data)
            return tool_result(success=False, source="nominatim.geocode+amap.fallback", error=result.get("error") or fallback.get("error"), data=data)
        if tool == "amap_poi_search":
            return poi_search(str(args.get("query") or ""), city=_string_or_none(args.get("city")), limit=_int_or_none(args.get("limit")) or 5)
        if tool == "amap_route_options":
            origin = args.get("origin") if isinstance(args.get("origin"), dict) else {}
            destination = args.get("destination") if isinstance(args.get("destination"), dict) else {}
            modes = tuple(mode for mode in _string_list(args.get("modes")) if mode in {"walking", "bicycling", "taxi", "transit"})
            return route_options(origin, destination, modes=modes or ("walking", "bicycling", "taxi", "transit"))
    except Exception as exc:  # noqa: BLE001 - tools must fail closed and surface to UI.
        return tool_result(success=False, source=str(tool or "unknown_tool"), error=str(exc), data={"arguments": args})
    return tool_result(success=False, source=str(tool or "unknown_tool"), error="Unsupported tool request.", data={"arguments": args})


def _route_requests_from_draft(draft: dict[str, Any], parsed_goal: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    requests = _sanitize_tool_requests(draft.get("route_tool_requests"))
    route = draft.get("route") if isinstance(draft.get("route"), list) else []
    warnings: list[str] = []
    route_dates = {str(item.get("date")) for item in route if isinstance(item, dict) and item.get("date")}
    long_or_regional = _is_long_or_regional_trip(parsed_goal or {}, str((parsed_goal or {}).get("raw_text") or ""))
    if long_or_regional or len(route_dates) > settings.agent_route_tools_max_dates or len(route) > 12:
        if requests or route:
            warnings.append("长周期/跨区域行程已跳过逐段地图路线工具，避免生成过慢；区间交通请以方案中的 route_note 和实际交通为准。")
        return [], warnings

    for first, second in zip(route, route[1:]):
        if not isinstance(first, dict) or not isinstance(second, dict):
            continue
        if first.get("date") != second.get("date"):
            continue
        if _point_has_coords(first) and _point_has_coords(second):
            requests.append(
                {
                    "tool": "amap_route_options",
                    "arguments": {
                        "origin": _route_point(first),
                        "destination": _route_point(second),
                        "modes": ["walking", "bicycling", "taxi", "transit"],
                    },
                    "reason": "Compute transfer time between adjacent route items.",
                }
            )
    deduped = _dedupe_tool_requests(requests)
    max_route = max(settings.agent_max_route_requests, 0)
    if len(deduped) > max_route:
        warnings.append(f"地图路线工具请求已从 {len(deduped)} 段限制为 {max_route} 段，避免长时间等待。")
        deduped = deduped[:max_route]
    return deduped, warnings


def _attach_transfer_results(route: list[dict[str, Any]], tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    route_pairs: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in tool_results:
        request = entry.get("request") or {}
        if request.get("tool") != "amap_route_options":
            continue
        arguments = request.get("arguments") if isinstance(request.get("arguments"), dict) else {}
        origin = arguments.get("origin") if isinstance(arguments.get("origin"), dict) else {}
        destination = arguments.get("destination") if isinstance(arguments.get("destination"), dict) else {}
        origin_date = str(origin.get("date") or "")
        destination_date = str(destination.get("date") or "")
        if origin_date and destination_date and origin_date != destination_date:
            continue
        result = entry.get("result") or {}
        data = result.get("data") or {}
        key = (
            origin_date,
            str(origin.get("spot_name") or origin.get("name") or data.get("from") or ""),
            str(destination.get("spot_name") or destination.get("name") or data.get("to") or ""),
        )
        if key[1:] != ("", ""):
            route_pairs[key] = data

    for index, item in enumerate(route[:-1]):
        next_item = route[index + 1]
        if item.get("date") != next_item.get("date"):
            item.pop("transfer_to_next", None)
            continue
        key = (
            str(item.get("date") or ""),
            str(item.get("spot_name") or item.get("name") or ""),
            str(next_item.get("spot_name") or next_item.get("name") or ""),
        )
        transfer = route_pairs.get(key)
        if transfer:
            recommended = transfer.get("recommended") or {}
            if _transfer_is_displayable(recommended):
                item["transfer_to_next"] = {
                    **recommended,
                    "summary": _display_transfer_summary(recommended),
                    "travel_options": transfer.get("options") or [],
                }
            else:
                item.pop("transfer_to_next", None)
        else:
            item.pop("transfer_to_next", None)
    return route


def _transfer_is_displayable(transfer: dict[str, Any]) -> bool:
    distance_m = _float_or_none(transfer.get("distance_m"))
    duration = _float_or_none(transfer.get("duration_minutes"))
    if distance_m is not None and distance_m > 120_000:
        return False
    if duration is not None and duration > 240:
        return False
    return True


def _display_transfer_summary(transfer: dict[str, Any]) -> str | None:
    distance_m = _float_or_none(transfer.get("distance_m"))
    duration = _float_or_none(transfer.get("duration_minutes"))
    if distance_m is not None and distance_m <= 80:
        return "同片区/同景区内现场移动，按步行 5-10 分钟预留。"
    if duration is not None and duration <= 1:
        return "同片区/同景区内现场移动，按步行 5-10 分钟预留。"
    summary = transfer.get("summary")
    return str(summary) if summary else None


def _sanitize_route_notes(route: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    removed_count = 0
    for item in route:
        note = item.get("route_note")
        if not isinstance(note, str) or not note.strip():
            continue
        if _contains_transport_estimate(note):
            item.pop("route_note", None)
            removed_count += 1
    if removed_count:
        warnings.append(f"Removed {removed_count} route_note transport estimate(s); transfer timing is rendered from map tools only.")
    return route, warnings


def _contains_transport_estimate(text: str) -> bool:
    compact = text.strip()
    if not compact:
        return False
    lowered = compact.lower()
    has_transport_word = any(word.lower() in lowered for word in TRANSPORT_WORDS)
    has_numeric_estimate = bool(NUMERIC_TRAVEL_RE.search(compact) or ASCII_TRAVEL_RE.search(compact))
    return bool(TRANSPORT_ESTIMATE_RE.search(compact) or (has_transport_word and has_numeric_estimate))


def _looks_like_combined_place(text: str) -> bool:
    if not text:
        return False
    if COMBINED_PLACE_RE.search(text):
        return True
    return "或" in text and len(text) >= 8


def _normalize_route(route: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(route, start=1):
        if not isinstance(raw, dict):
            continue
        name = raw.get("spot_name") or raw.get("name") or raw.get("place_name")
        item = {
            "item_id": str(raw.get("item_id") or f"route-{index}"),
            "option_id": str(raw.get("option_id") or f"route-{index}"),
            "sequence": index,
            "date": _string_or_none(raw.get("date")),
            "start_time": str(raw.get("start_time") or ""),
            "end_time": str(raw.get("end_time") or ""),
            "spot_name": str(name or "待确认地点"),
            "city": _string_or_none(raw.get("city")),
            "lat": _float_or_none(raw.get("lat") or raw.get("latitude")),
            "lng": _float_or_none(raw.get("lng") or raw.get("longitude")),
            "shoot_goal": str(raw.get("shoot_goal") or raw.get("goal") or ""),
            "light_label": _string_or_none(raw.get("light_label")),
            "location_hint": _string_or_none(raw.get("location_hint") or raw.get("address")),
            "route_note": _string_or_none(raw.get("route_note") or raw.get("note")),
            "final_score": _float_or_none(raw.get("final_score")) or 0,
            "guide": raw.get("guide") if isinstance(raw.get("guide"), dict) else {},
        }
        normalized.append(item)
    return normalized


def _build_map_context(route: list[dict[str, Any]], tool_results: list[dict[str, Any]]) -> dict[str, Any]:
    route_transfers = []
    for entry in tool_results:
        if (entry.get("request") or {}).get("tool") == "amap_route_options":
            data = (entry.get("result") or {}).get("data") or {}
            if data:
                route_transfers.append(data)
    return {
        "route_transfers": route_transfers,
        "geo_summary": {
            "route_spot_count": len(route),
            "geo_verified_count": sum(1 for item in route if item.get("lat") is not None and item.get("lng") is not None),
            "missing_geo_count": sum(1 for item in route if item.get("lat") is None or item.get("lng") is None),
        },
    }


def _build_reference_context(reference_spots: list[dict[str, Any]], tool_results: list[dict[str, Any]]) -> dict[str, Any]:
    search_results = []
    for entry in tool_results:
        if (entry.get("request") or {}).get("tool") == "tavily_search":
            search_results.extend(((entry.get("result") or {}).get("data") or {}).get("results") or [])
    return {
        "seed_spots": _compact_reference_spots(reference_spots),
        "results": search_results,
        "note": "Seed spots are optional references for the LLM, not mandatory output.",
    }


def _compact_reference_spots(spots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for spot in spots[:8]:
        compact.append(
            {
                "name": _clip_text(spot.get("name"), 80),
                "city": _clip_text(spot.get("city"), 40),
                "lat": spot.get("lat"),
                "lng": spot.get("lng"),
                "geo_verified": spot.get("geo_verified"),
                "styles": _trim_data((spot.get("suitable_styles") or [])[:3], max_list=3, max_string=80),
                "visual_elements": _trim_data((spot.get("visual_elements") or spot.get("themes") or [])[:4], max_list=4, max_string=80),
                "best_time_hint": _trim_data((spot.get("best_time_hint") or [])[:2], max_list=2, max_string=80),
                "tips": _trim_data((spot.get("shooting_tips") or [])[:2], max_list=2, max_string=120),
                "score": spot.get("match_score") or spot.get("base_photo_score"),
            }
        )
    return compact


def _compact_tool_results(tool_results: list[dict[str, Any]], *, max_items: int = 12) -> list[dict[str, Any]]:
    compact = []
    for entry in tool_results[-max_items:]:
        result = entry.get("result") or {}
        data = result.get("data") or {}
        compact.append(
            {
                "tool": (entry.get("request") or {}).get("tool"),
                "arguments": _compact_tool_arguments((entry.get("request") or {}).get("tool"), (entry.get("request") or {}).get("arguments") or {}),
                "success": result.get("success"),
                "source": result.get("source"),
                "error": _clip_text(result.get("error"), 160),
                "data": _trim_data(data),
            }
        )
    return compact


def _compact_tool_arguments(tool: str | None, args: dict[str, Any]) -> dict[str, Any]:
    if tool in {"weather_lookup", "sunlight_lookup"}:
        return {
            "destination": _clip_text(args.get("destination"), 80),
            "date_range": _string_list(args.get("date_range"))[:5],
            "lat": args.get("lat"),
            "lng": args.get("lng"),
        }
    if tool in {"tavily_search", "nominatim_geocode", "amap_poi_search"}:
        return {
            "query": _clip_text(args.get("query"), 120),
            "city": _clip_text(args.get("city"), 40),
            "limit": args.get("limit") or args.get("max_results"),
        }
    if tool == "amap_route_options":
        return {
            "origin": _compact_point(args.get("origin")),
            "destination": _compact_point(args.get("destination")),
            "modes": _string_list(args.get("modes"))[:4],
        }
    return _trim_data(args, max_list=4, max_string=120)


def _compact_point(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "name": _clip_text(value.get("name") or value.get("spot_name"), 80),
        "city": _clip_text(value.get("city"), 40),
        "lat": value.get("lat"),
        "lng": value.get("lng"),
    }


def _trim_data(data: Any, *, max_list: int = 4, max_string: int = 320) -> Any:
    if isinstance(data, dict):
        trimmed: dict[str, Any] = {}
        for key, value in data.items():
            if key in {"url", "request_id", "raw", "raw_content"}:
                continue
            if key in {"hourly"}:
                trimmed[key] = _trim_data(value[:4] if isinstance(value, list) else value, max_list=max_list, max_string=max_string)
            elif key in {"results", "pois", "options", "daily"}:
                trimmed[key] = _trim_data(value[:max_list] if isinstance(value, list) else value, max_list=max_list, max_string=max_string)
            elif isinstance(value, dict):
                trimmed[key] = _trim_data(value, max_list=max_list, max_string=max_string)
            elif isinstance(value, list):
                trimmed[key] = _trim_data(value[:max_list], max_list=max_list, max_string=max_string)
            elif isinstance(value, str):
                trimmed[key] = _clip_text(value, max_string)
            else:
                trimmed[key] = value
        return trimmed
    if isinstance(data, list):
        return [_trim_data(item, max_list=max_list, max_string=max_string) for item in data[:max_list]]
    if isinstance(data, str):
        return _clip_text(data, max_string)
    return data


def _clip_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _compact_plan_for_followup(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_id": plan.get("plan_id"),
        "user_input": _clip_text(plan.get("user_input"), 500),
        "parsed_goal": plan.get("parsed_goal") or {},
        "route": (plan.get("route") or [])[:12],
        "warnings": plan.get("warnings") or [],
        "final_markdown": _clip_text(plan.get("final_markdown"), 3000),
        "weather_context": plan.get("weather_context") or {},
        "sunlight_context": plan.get("sunlight_context") or {},
        "map_context": plan.get("map_context") or {},
    }


def _task_plan_from_tools(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": (entry.get("request") or {}).get("tool"),
            "status": "done" if (entry.get("result") or {}).get("success") else "degraded",
            "note": (entry.get("request") or {}).get("reason") or (entry.get("result") or {}).get("error") or "",
        }
        for entry in tool_results
    ]


def _tool_observation(result: ToolResult) -> dict[str, Any]:
    data = result.get("data") or {}
    observation = {
        "source": result.get("source"),
        "success": result.get("success"),
        "error": result.get("error"),
    }
    for key in ("query", "count", "summary", "from", "to"):
        if key in data:
            observation[key] = data[key]
    if "results" in data:
        observation["result_count"] = len(data.get("results") or [])
    if "pois" in data:
        observation["result_count"] = len(data.get("pois") or [])
    if "options" in data:
        observation["option_count"] = len(data.get("options") or [])
    return observation


def _tool_warning(request: dict[str, Any], result: ToolResult) -> str | None:
    if result.get("success"):
        return None
    return f"Tool {request.get('tool')} failed or used fallback: {result.get('error') or 'unknown error'}"


def _tool_warnings(tool_results: list[dict[str, Any]]) -> list[str]:
    return [entry["warning"] for entry in tool_results if entry.get("warning")]


def _first_tool_data(tool_results: list[dict[str, Any]], tool_name: str, key: str) -> dict[str, Any]:
    for entry in tool_results:
        if (entry.get("request") or {}).get("tool") != tool_name:
            continue
        data = (entry.get("result") or {}).get("data") or {}
        if isinstance(data.get(key), dict):
            return data[key]
    return {}


def _fallback_markdown(payload: dict[str, Any], warnings: list[str]) -> str:
    lines = [str(payload.get("answer_summary") or payload.get("reason") or "当前工具证据不足，无法生成完整方案。")]
    unable = payload.get("unable_to_satisfy") or []
    if unable:
        lines.extend(["", "无法满足的原因：", *[f"- {item}" for item in unable]])
    if warnings:
        lines.extend(["", "需要注意：", *[f"- {item}" for item in warnings]])
    return "\n".join(lines)


def _render_completed_markdown(
    *,
    parsed_goal: dict[str, Any],
    weather_context: dict[str, Any],
    sunlight_context: dict[str, Any],
    route: list[dict[str, Any]],
    backup_plan: list[dict[str, Any]],
    warnings: list[str],
    assumptions: list[str],
    tool_failures: list[dict[str, Any]],
) -> str:
    destination = parsed_goal.get("destination") or "目的地"
    dates = _string_list(parsed_goal.get("date_range"))
    styles = _string_list(parsed_goal.get("shooting_style"))
    elements = _string_list(parsed_goal.get("visual_elements"))
    equipment = _string_list(parsed_goal.get("equipment"))

    lines = [
        f"# {destination}写真行程与拍摄计划",
        "",
        "## 总体安排",
        f"- 日期：{_format_date_range(dates)}",
        f"- 风格：{_join_or_wait(styles)}",
        f"- 画面重点：{_join_or_wait(elements)}",
        f"- 器材：{_join_or_wait(equipment)}",
    ]
    if weather_context.get("summary"):
        lines.append(f"- 天气判断：{weather_context.get('summary')}")
    if sunlight_context.get("summary"):
        lines.append(f"- 光线窗口：{sunlight_context.get('summary')}")

    image_lines = _image_analysis_summary_lines(parsed_goal.get("image_analysis"))
    if image_lines:
        lines.extend(["", "## 参考图理解", *image_lines])

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in route:
        grouped.setdefault(str(item.get("date") or "日期待确认"), []).append(item)

    lines.extend(["", "## 每日路线"])
    for day in _ordered_daily_dates(dates, grouped):
        items = grouped.get(day) or []
        lines.extend(["", f"### {day}"])
        if not items:
            lines.append("- 这一天没有可执行路线，需重新确认。")
            continue
        for item in items:
            transfer = item.get("transfer_to_next") or {}
            transfer_summary = _display_transfer_summary(transfer) if transfer else None
            transfer_text = f" 下一段移动：{transfer_summary}" if transfer_summary else ""
            route_note_text = f" 提醒：{item.get('route_note')}" if item.get("route_note") else ""
            lines.append(
                f"- {item.get('start_time')}-{item.get('end_time')} | {item.get('spot_name')}："
                f"{item.get('shoot_goal') or '写真拍摄'}{transfer_text}{route_note_text}"
            )

    lines.extend(["", "## 分段拍摄方案"])
    for item in route:
        guide = item.get("guide") if isinstance(item.get("guide"), dict) else {}
        lines.extend(
            [
                "",
                f"### {item.get('date')} {item.get('start_time')}-{item.get('end_time')} {item.get('spot_name')}",
                f"- 拍摄目标：{item.get('shoot_goal') or '人物写真'}",
                f"- 光线/机位：{item.get('light_label') or '按现场光线调整'}；{item.get('location_hint') or item.get('route_note') or '现场确认最佳角度'}",
                f"- 人物站位：{_guide_text(guide, ['subject_position', 'person_position', '人物站位', 'subject'])}",
                f"- 摄影机位：{_guide_text(guide, ['photographer_position', 'camera_position', '摄影机位', '机位'])}",
                f"- 构图动作：{_guide_text(guide, ['composition', 'composition_notes', 'poses', 'actions', '动作', '构图'])}",
                f"- 镜头器材：{_guide_text(guide, ['lens', 'equipment', 'equipment_tip', '镜头', '器材'])}",
                f"- 现场提醒：{_guide_text(guide, ['safety_notes', 'crowd_note', 'weather_note', '安全', '注意'])}",
            ]
        )

    if backup_plan:
        lines.extend(["", "## 备用方案"])
        for item in backup_plan[:6]:
            if isinstance(item, dict):
                lines.append(f"- {item.get('trigger') or '触发条件'}：{item.get('action') or '现场调整'}")
            else:
                lines.append(f"- {item}")

    risk_lines = _unique_strings(
        [
            *warnings,
            *assumptions,
            *[
                f"{(entry.get('request') or {}).get('tool')} 工具失败或降级：{(entry.get('result') or {}).get('error')}"
                for entry in tool_failures
            ],
        ]
    )
    if risk_lines:
        lines.extend(["", "## 风险与不确定性"])
        lines.extend(f"- {item}" for item in risk_lines[:10])

    return "\n".join(lines)


def _guide_text(guide: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = guide.get(key)
        if isinstance(value, list):
            text = "；".join(str(item) for item in value if str(item).strip())
        elif value is None:
            text = ""
        else:
            text = str(value)
        if text.strip():
            return text.strip()
    if guide:
        return "；".join(f"{key}: {value}" for key, value in list(guide.items())[:3])
    return "待现场根据人流和光线微调。"


def _image_analysis_summary_lines(image_analysis: Any) -> list[str]:
    if not isinstance(image_analysis, dict) or not image_analysis:
        return []
    lines: list[str] = []
    style = _image_analysis_text(image_analysis, ["style_summary", "description"])
    if style:
        lines.append(f"- 风格/氛围：{style}")
    fields = [
        ("光线", ["lighting"]),
        ("构图", ["composition"]),
        ("动作", ["pose_action", "poses"]),
        ("色彩/服装", ["color_palette", "clothing_props"]),
        ("可复刻场景", ["location_types", "possible_location_types"]),
        ("复刻要点", ["replication_notes"]),
    ]
    for label, keys in fields:
        text = _image_analysis_text(image_analysis, keys)
        if text:
            lines.append(f"- {label}：{text}")
    return lines[:6]


def _image_analysis_text(image_analysis: dict[str, Any], keys: list[str]) -> str:
    parts: list[str] = []
    for key in keys:
        value = image_analysis.get(key)
        if isinstance(value, list):
            parts.extend(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "；".join(_unique_strings(parts)[:4])


def _format_date_range(values: list[str]) -> str:
    if not values:
        return "待确认"
    if len(values) <= 3:
        return "、".join(values)
    return f"{values[0]} 至 {values[-1]}（共 {len(values)} 天）"


def _ordered_daily_dates(dates: list[str], grouped: dict[str, list[dict[str, Any]]]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in [*dates, *grouped.keys()]:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    if not result:
        return ["日期待确认"]
    known = [value for value in result if _parse_iso_date(value) is not None]
    unknown = [value for value in result if _parse_iso_date(value) is None]
    return sorted(known) + unknown


def _join_or_wait(values: list[str]) -> str:
    return "、".join(values) if values else "待确认"


def _is_terminal_draft(draft: dict[str, Any]) -> bool:
    return draft.get("status") in {"final", "completed", "cannot_satisfy"} and bool(draft.get("markdown"))


def _point_has_coords(item: dict[str, Any]) -> bool:
    return _float_or_none(item.get("lat") or item.get("latitude")) is not None and _float_or_none(item.get("lng") or item.get("longitude")) is not None


def _route_point(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("spot_name") or item.get("name") or item.get("place_name"),
        "spot_name": item.get("spot_name") or item.get("name") or item.get("place_name"),
        "date": item.get("date"),
        "city": item.get("city"),
        "lat": _float_or_none(item.get("lat") or item.get("latitude")),
        "lng": _float_or_none(item.get("lng") or item.get("longitude")),
        "geo_verified": _point_has_coords(item),
    }


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if item is not None and str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
