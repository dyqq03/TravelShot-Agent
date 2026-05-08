from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    plan_id: str
    user_input: str
    reference_images: list[str]
    parsed_goal: dict[str, Any]
    task_plan: list[dict[str, Any]]
    current_task: dict[str, Any]
    agent_steps: list[dict[str, Any]]
    candidate_spots: list[dict[str, Any]]
    candidate_spots_source: str
    weather_context: dict[str, Any]
    sunlight_context: dict[str, Any]
    map_context: dict[str, Any]
    reference_context: dict[str, Any]
    discovery_context: dict[str, Any]
    image_analysis: dict[str, Any]
    repair_context: dict[str, Any]
    visual_goal: dict[str, Any]
    spot_time_options: list[dict[str, Any]]
    scored_options: list[dict[str, Any]]
    optimized_route: list[dict[str, Any]]
    backup_plan: list[dict[str, Any]]
    final_markdown: str
    warnings: list[str]
    llm_used: bool
    llm_call_count: int
