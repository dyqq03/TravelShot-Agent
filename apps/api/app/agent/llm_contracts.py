from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


ALLOWED_TOOLS = {
    "weather_lookup",
    "sunlight_lookup",
    "tavily_search",
    "nominatim_geocode",
    "amap_poi_search",
    "amap_route_options",
}

TRAVEL_MODES = {"walking", "bicycling", "taxi", "transit"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")


@dataclass(frozen=True)
class LLMContract:
    name: str
    schema: str
    validator: Callable[[dict[str, Any]], list[str]]


def validate_contract(name: str, payload: dict[str, Any]) -> list[str]:
    contract = CONTRACTS.get(name)
    if contract is None:
        return []
    return contract.validator(payload)


def contract_schema(name: str) -> str:
    contract = CONTRACTS.get(name)
    return contract.schema if contract else "{}"


def _intent_errors(payload: dict[str, Any]) -> list[str]:
    errors = _required(payload, ["destination", "departure_city", "date_range", "duration_days", "shooting_style", "visual_elements", "subject", "equipment", "explicit_locations", "must_satisfy", "constraints", "unknowns", "image_analysis", "tool_requests"])
    errors.extend(_nullable_string(payload, "destination"))
    errors.extend(_nullable_string(payload, "departure_city"))
    errors.extend(_date_list(payload, "date_range"))
    errors.extend(_nullable_number(payload, "duration_days"))
    for key in ["shooting_style", "visual_elements", "subject", "equipment", "must_satisfy", "constraints", "unknowns"]:
        errors.extend(_string_list(payload, key))
    if not isinstance(payload.get("explicit_locations"), list):
        errors.append("explicit_locations must be an array.")
    else:
        for index, item in enumerate(payload["explicit_locations"]):
            if not isinstance(item, dict):
                errors.append(f"explicit_locations[{index}] must be an object.")
                continue
            if not _non_empty_string(item.get("name")):
                errors.append(f"explicit_locations[{index}].name must be a non-empty string.")
            if item.get("city") is not None and not isinstance(item.get("city"), str):
                errors.append(f"explicit_locations[{index}].city must be string or null.")
            if not isinstance(item.get("must_keep"), bool):
                errors.append(f"explicit_locations[{index}].must_keep must be boolean.")
            if not isinstance(item.get("reason"), str):
                errors.append(f"explicit_locations[{index}].reason must be a string.")
    if not isinstance(payload.get("image_analysis"), dict):
        errors.append("image_analysis must be an object.")
    errors.extend(_tool_requests(payload.get("tool_requests"), "tool_requests"))
    return errors


def _draft_errors(payload: dict[str, Any]) -> list[str]:
    errors = _required(payload, ["status", "reason", "tool_requests", "route", "warnings", "unable_to_satisfy"])
    if payload.get("status") not in {"need_more_tools", "final", "cannot_satisfy"}:
        errors.append("status must be need_more_tools, final, or cannot_satisfy.")
    if not isinstance(payload.get("reason"), str):
        errors.append("reason must be a string.")
    errors.extend(_tool_requests(payload.get("tool_requests"), "tool_requests"))
    errors.extend(_route(payload.get("route"), require_complete=False))
    errors.extend(_string_list(payload, "warnings"))
    errors.extend(_string_list(payload, "unable_to_satisfy"))
    return errors


def _final_errors(payload: dict[str, Any]) -> list[str]:
    errors = _required(payload, ["status", "answer_summary", "markdown", "route", "task_plan", "backup_plan", "warnings", "unable_to_satisfy", "assumptions", "evidence_refs", "confidence"])
    if payload.get("status") not in {"completed", "cannot_satisfy"}:
        errors.append("status must be completed or cannot_satisfy.")
    if not isinstance(payload.get("answer_summary"), str):
        errors.append("answer_summary must be a string.")
    if payload.get("markdown") is not None and not isinstance(payload.get("markdown"), str):
        errors.append("markdown must be string or null.")
    route_errors = _route(payload.get("route"), require_complete=payload.get("status") == "completed")
    errors.extend(route_errors)
    for key in ["warnings", "unable_to_satisfy", "assumptions", "evidence_refs"]:
        errors.extend(_string_list(payload, key))
    if not isinstance(payload.get("task_plan"), list):
        errors.append("task_plan must be an array.")
    if not isinstance(payload.get("backup_plan"), list):
        errors.append("backup_plan must be an array.")
    errors.extend(_nullable_number(payload, "confidence", allow_required=True))
    return errors


def _followup_intent_errors(payload: dict[str, Any]) -> list[str]:
    errors = _required(payload, ["summary", "tool_requests", "warnings"])
    if not isinstance(payload.get("summary"), str):
        errors.append("summary must be a string.")
    errors.extend(_tool_request_shells(payload.get("tool_requests"), "tool_requests"))
    errors.extend(_string_list(payload, "warnings"))
    return errors


def _followup_answer_errors(payload: dict[str, Any]) -> list[str]:
    errors = _required(payload, ["status", "answer", "changes", "warnings"])
    if payload.get("status") not in {"answered", "cannot_satisfy"}:
        errors.append("status must be answered or cannot_satisfy.")
    if not _non_empty_string(payload.get("answer")):
        errors.append("answer must be a non-empty string.")
    if not isinstance(payload.get("changes"), list):
        errors.append("changes must be an array.")
    else:
        for index, item in enumerate(payload["changes"]):
            if not isinstance(item, dict):
                errors.append(f"changes[{index}] must be an object.")
                continue
            for key in ["section", "change", "reason"]:
                if not isinstance(item.get(key), str):
                    errors.append(f"changes[{index}].{key} must be a string.")
    errors.extend(_string_list(payload, "warnings"))
    return errors


def _tool_requests(value: Any, path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, list):
        return [f"{path} must be an array."]
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_path} must be an object.")
            continue
        tool = item.get("tool")
        args = item.get("arguments")
        if tool not in ALLOWED_TOOLS:
            errors.append(f"{item_path}.tool must be one of {sorted(ALLOWED_TOOLS)}.")
            continue
        if not isinstance(args, dict):
            errors.append(f"{item_path}.arguments must be an object.")
            continue
        if not isinstance(item.get("reason"), str):
            errors.append(f"{item_path}.reason must be a string.")
        errors.extend(_tool_arguments(tool, args, f"{item_path}.arguments"))
    return errors


def _tool_request_shells(value: Any, path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, list):
        return [f"{path} must be an array."]
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_path} must be an object.")
            continue
        if item.get("tool") not in ALLOWED_TOOLS:
            errors.append(f"{item_path}.tool must be one of {sorted(ALLOWED_TOOLS)}.")
        if not isinstance(item.get("arguments"), dict):
            errors.append(f"{item_path}.arguments must be an object.")
        if not isinstance(item.get("reason"), str):
            errors.append(f"{item_path}.reason must be a string.")
    return errors


def _tool_arguments(tool: str, args: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    if tool in {"weather_lookup", "sunlight_lookup"}:
        if not _non_empty_string(args.get("destination")):
            errors.append(f"{path}.destination must be a non-empty string.")
        errors.extend(_date_list(args, "date_range", path=path))
        for key in ["lat", "lng"]:
            if args.get(key) is not None and not isinstance(args.get(key), int | float):
                errors.append(f"{path}.{key} must be a number when present.")
    elif tool == "tavily_search":
        if not _non_empty_string(args.get("query")):
            errors.append(f"{path}.query must be a non-empty string.")
        if args.get("max_results") is not None and not isinstance(args.get("max_results"), int | float):
            errors.append(f"{path}.max_results must be a number when present.")
    elif tool in {"nominatim_geocode", "amap_poi_search"}:
        if not _non_empty_string(args.get("query")):
            errors.append(f"{path}.query must be a non-empty string.")
        if args.get("city") is not None and not isinstance(args.get("city"), str):
            errors.append(f"{path}.city must be string or null.")
        if args.get("limit") is not None and not isinstance(args.get("limit"), int | float):
            errors.append(f"{path}.limit must be a number when present.")
    elif tool == "amap_route_options":
        errors.extend(_route_point(args.get("origin"), f"{path}.origin"))
        errors.extend(_route_point(args.get("destination"), f"{path}.destination"))
        modes = args.get("modes")
        if not isinstance(modes, list) or not modes:
            errors.append(f"{path}.modes must be a non-empty array.")
        else:
            for index, mode in enumerate(modes):
                if mode not in TRAVEL_MODES:
                    errors.append(f"{path}.modes[{index}] must be one of {sorted(TRAVEL_MODES)}.")
    return errors


def _route(value: Any, require_complete: bool) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, list):
        return ["route must be an array."]
    if require_complete and not value:
        errors.append("route must contain at least one item when status is completed.")
    for index, item in enumerate(value):
        path = f"route[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path} must be an object.")
            continue
        if item.get("date") is not None and (not isinstance(item.get("date"), str) or not DATE_RE.match(item["date"])):
            errors.append(f"{path}.date must be YYYY-MM-DD or null.")
        for key in ["start_time", "end_time"]:
            if item.get(key) is not None and (not isinstance(item.get(key), str) or not TIME_RE.match(item[key])):
                errors.append(f"{path}.{key} must be HH:MM.")
        for key in ["spot_name", "shoot_goal"]:
            if require_complete and not _non_empty_string(item.get(key)):
                errors.append(f"{path}.{key} must be a non-empty string.")
            elif item.get(key) is not None and not isinstance(item.get(key), str):
                errors.append(f"{path}.{key} must be a string.")
        for key in ["city", "light_label", "location_hint", "route_note"]:
            if item.get(key) is not None and not isinstance(item.get(key), str):
                errors.append(f"{path}.{key} must be string or null.")
        for key in ["lat", "lng"]:
            if item.get(key) is not None and not isinstance(item.get(key), int | float):
                errors.append(f"{path}.{key} must be number or null.")
        guide = item.get("guide")
        if guide is not None and not isinstance(guide, dict):
            errors.append(f"{path}.guide must be an object.")
        elif require_complete:
            errors.extend(_guide(guide, f"{path}.guide"))
    return errors


def _guide(value: Any, path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path} must be an object."]
    required = ["subject_position", "photographer_position", "composition", "poses", "lens", "safety_notes"]
    errors = [f"{path}.{key} is required for completed route items." for key in required if key not in value]
    for key in ["subject_position", "photographer_position", "composition", "lens", "safety_notes"]:
        if key in value and not isinstance(value.get(key), str):
            errors.append(f"{path}.{key} must be a string.")
    poses = value.get("poses")
    if "poses" in value:
        if not isinstance(poses, list):
            errors.append(f"{path}.poses must be an array of strings.")
        else:
            for index, item in enumerate(poses):
                if not isinstance(item, str):
                    errors.append(f"{path}.poses[{index}] must be a string.")
    return errors


def _route_point(value: Any, path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return [f"{path} must be an object."]
    if not _non_empty_string(value.get("name")) and not _non_empty_string(value.get("spot_name")):
        errors.append(f"{path}.name must be a non-empty string.")
    for key in ["lat", "lng"]:
        if not isinstance(value.get(key), int | float):
            errors.append(f"{path}.{key} must be a number.")
    if value.get("city") is not None and not isinstance(value.get("city"), str):
        errors.append(f"{path}.city must be string or null.")
    return errors


def _required(payload: dict[str, Any], keys: list[str]) -> list[str]:
    return [f"{key} is required." for key in keys if key not in payload]


def _nullable_string(payload: dict[str, Any], key: str) -> list[str]:
    if payload.get(key) is None or isinstance(payload.get(key), str):
        return []
    return [f"{key} must be string or null."]


def _nullable_number(payload: dict[str, Any], key: str, allow_required: bool = False) -> list[str]:
    value = payload.get(key)
    if value is None and (allow_required or key in payload):
        return []
    if isinstance(value, int | float):
        return []
    return [f"{key} must be number or null."]


def _string_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return [f"{key} must be an array of strings."]
    return [f"{key}[{index}] must be a string." for index, item in enumerate(value) if not isinstance(item, str)]


def _date_list(payload: dict[str, Any], key: str, path: str | None = None) -> list[str]:
    value = payload.get(key)
    label = f"{path}.{key}" if path else key
    if not isinstance(value, list):
        return [f"{label} must be an array of YYYY-MM-DD strings."]
    errors = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not DATE_RE.match(item):
            errors.append(f"{label}[{index}] must be YYYY-MM-DD.")
    return errors


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


CONTRACTS = {
    "intent_analysis": LLMContract(
        name="intent_analysis",
        schema="""
{
  "destination": string|null,
  "departure_city": string|null,
  "date_range": ["YYYY-MM-DD"],
  "duration_days": number|null,
  "shooting_style": [string],
  "visual_elements": [string],
  "subject": [string],
  "equipment": [string],
  "explicit_locations": [{"name": string, "city": string|null, "must_keep": boolean, "reason": string}],
  "must_satisfy": [string],
  "constraints": [string],
  "unknowns": [string],
  "image_analysis": {"description": string|null, "style_summary": string|null, "lighting": [string], "composition": [string], "pose_action": [string], "color_palette": [string], "clothing_props": [string], "location_types": [string], "replication_notes": [string]},
  "tool_requests": [{"tool": allowed_tool, "arguments": object, "reason": string}]
}
""".strip(),
        validator=_intent_errors,
    ),
    "draft_plan": LLMContract(
        name="draft_plan",
        schema="""
{
  "status": "need_more_tools"|"final"|"cannot_satisfy",
  "reason": string,
  "tool_requests": [{"tool": allowed_tool, "arguments": object, "reason": string}],
  "route": [{"date": "YYYY-MM-DD", "start_time": "HH:MM", "end_time": "HH:MM", "spot_name": string, "city": string|null, "lat": number|null, "lng": number|null, "shoot_goal": string, "light_label": string|null, "location_hint": string|null, "guide": {"subject_position": string, "photographer_position": string, "composition": string, "poses": [string], "lens": string, "safety_notes": string}}],
  "warnings": [string],
  "unable_to_satisfy": [string]
}
""".strip(),
        validator=_draft_errors,
    ),
    "final_plan": LLMContract(
        name="final_plan",
        schema="""
{
  "status": "completed"|"cannot_satisfy",
  "answer_summary": string,
  "markdown": string|null,
  "route": [{"date": "YYYY-MM-DD", "start_time": "HH:MM", "end_time": "HH:MM", "spot_name": string, "city": string|null, "lat": number|null, "lng": number|null, "shoot_goal": string, "light_label": string|null, "location_hint": string|null, "route_note": string|null, "guide": {"subject_position": string, "photographer_position": string, "composition": string, "poses": [string], "lens": string, "safety_notes": string}}],
  "task_plan": [{"title": string, "status": string, "note": string}],
  "backup_plan": [{"trigger": string, "action": string}],
  "warnings": [string],
  "unable_to_satisfy": [string],
  "assumptions": [string],
  "evidence_refs": [string],
  "confidence": number|null
}
""".strip(),
        validator=_final_errors,
    ),
    "followup_intent": LLMContract(
        name="followup_intent",
        schema='{"summary": string, "tool_requests": [{"tool": allowed_tool, "arguments": object, "reason": string}], "warnings": [string]}',
        validator=_followup_intent_errors,
    ),
    "followup_answer": LLMContract(
        name="followup_answer",
        schema='{"status": "answered"|"cannot_satisfy", "answer": string, "changes": [{"section": string, "change": string, "reason": string}], "warnings": [string]}',
        validator=_followup_answer_errors,
    ),
}
