from __future__ import annotations

import json
import math
from typing import Any

from app.spot.cities import CITY_PROFILES


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _json_list(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _record_to_dict(record: Any) -> dict[str, Any]:
    item = dict(record)
    for key, value in list(item.items()):
        item[key] = _maybe_json(value)
    return item


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def upsert_photo_spots(pool: Any, spots: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> int:
    if not spots:
        return 0

    query = """
        INSERT INTO photo_spots (
          id, city, name, address, latitude, longitude, geo_verified, spot_type,
          suitable_styles, visual_elements, best_time_hint, weather_preference,
          ticket_required, ticket_note, opening_hours, crowd_risk, phone_friendly,
          base_photo_score, shooting_tips, source_type, source_urls, raw, updated_at
        )
        VALUES (
          $1, $2, $3, $4, $5, $6, $7, $8,
          $9::jsonb, $10::jsonb, $11::jsonb, $12::jsonb,
          $13, $14, $15::jsonb, $16, $17,
          $18, $19::jsonb, $20, $21::jsonb, $22::jsonb, NOW()
        )
        ON CONFLICT (id) DO UPDATE SET
          city = EXCLUDED.city,
          name = EXCLUDED.name,
          address = EXCLUDED.address,
          latitude = EXCLUDED.latitude,
          longitude = EXCLUDED.longitude,
          geo_verified = EXCLUDED.geo_verified,
          spot_type = EXCLUDED.spot_type,
          suitable_styles = EXCLUDED.suitable_styles,
          visual_elements = EXCLUDED.visual_elements,
          best_time_hint = EXCLUDED.best_time_hint,
          weather_preference = EXCLUDED.weather_preference,
          ticket_required = EXCLUDED.ticket_required,
          ticket_note = EXCLUDED.ticket_note,
          opening_hours = EXCLUDED.opening_hours,
          crowd_risk = EXCLUDED.crowd_risk,
          phone_friendly = EXCLUDED.phone_friendly,
          base_photo_score = EXCLUDED.base_photo_score,
          shooting_tips = EXCLUDED.shooting_tips,
          source_type = EXCLUDED.source_type,
          source_urls = EXCLUDED.source_urls,
          raw = EXCLUDED.raw,
          updated_at = NOW()
    """
    args = [
        (
            spot["spot_id"],
            spot["city"],
            spot["name"],
            spot.get("location_hint"),
            spot.get("lat"),
            spot.get("lng"),
            bool(spot.get("geo_verified")),
            spot.get("spot_type"),
            _json_list(spot.get("suitable_styles")),
            _json_list(spot.get("visual_elements")),
            _json_list(spot.get("best_time_hint")),
            _json_list(spot.get("weather_preference")),
            bool(spot.get("ticket_required")),
            spot.get("access_and_notes"),
            _json(spot.get("opening_hours")),
            spot.get("crowd_risk"),
            bool(spot.get("phone_friendly", True)),
            spot.get("base_photo_score"),
            _json_list(spot.get("shooting_tips")),
            "seed",
            _json_list(spot.get("source_urls")),
            _json(spot),
        )
        for spot in spots
    ]
    async with pool.acquire() as conn:
        await conn.executemany(query, args)
    return len(args)


async def search_photo_spots(pool: Any, parsed_goal: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    city = parsed_goal.get("destination") or "杭州"
    if city and city != "待推荐":
        rows = await _fetch_city_spots(pool, city)
    else:
        rows = await _fetch_all_spots(pool)

    styles = set(parsed_goal.get("shooting_style") or [])
    elements = set(parsed_goal.get("visual_elements") or [])
    required_external_scenes = elements.intersection({"沙漠", "雪山", "草原"})
    user_text = str(parsed_goal.get("raw_text") or "")
    scored: list[dict[str, Any]] = []
    for row in rows:
        spot = _db_spot_to_agent_spot(row)
        style_matches = styles.intersection(spot.get("suitable_styles") or [])
        element_matches = elements.intersection(spot.get("visual_elements") or [])
        raw_text = f"{spot['name']} {spot['location_hint']} {' '.join(spot.get('themes') or [])} {' '.join(spot.get('shooting_tips') or [])}"
        fuzzy_matches = sum(1 for item in styles.union(elements) if item and item in raw_text)
        if city not in CITY_PROFILES and required_external_scenes and not any(scene in raw_text for scene in required_external_scenes):
            continue
        match_score = (
            spot.get("base_photo_score", 7.5)
            + len(style_matches) * 0.8
            + len(element_matches) * 0.9
            + fuzzy_matches * 0.35
            + spot.get("source_confidence", 0.7)
        )
        exact_user_match = False
        if _compact(spot["name"]) and _compact(spot["name"]) in _compact(user_text):
            match_score += 3.0
            exact_user_match = True
        elif any(_compact(token) in _compact(user_text) for token in _important_name_tokens(spot["name"])):
            match_score += 1.2
            exact_user_match = True
        spot["match_score"] = round(min(match_score, 10.0), 2)
        spot["exact_user_match"] = exact_user_match
        spot["match_reasons"] = list(style_matches) + list(element_matches)
        if not spot["match_reasons"]:
            spot["match_reasons"] = spot.get("themes", [])[:2]
        scored.append(spot)

    scored.sort(key=lambda item: (bool(item.get("exact_user_match")), item["match_score"]), reverse=True)
    return scored[:limit]


async def _fetch_city_spots(pool: Any, city: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT *
            FROM photo_spots
            WHERE city = $1
            ORDER BY base_photo_score DESC NULLS LAST, name
            """,
            city,
        )
    return [_record_to_dict(row) for row in rows]


async def _fetch_all_spots(pool: Any) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT *
            FROM photo_spots
            ORDER BY base_photo_score DESC NULLS LAST, name
            """
        )
    return [_record_to_dict(row) for row in rows]


def _compact(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum() or "\u4e00" <= ch <= "\u9fff").lower()


def _important_name_tokens(name: str) -> list[str]:
    compact = _compact(name)
    tokens = []
    for size in range(5, 1, -1):
        tokens.extend(compact[index:index + size] for index in range(max(len(compact) - size + 1, 0)))
    return [token for token in tokens if len(token) >= 2][:18]


def _distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    earth_radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * earth_radius_km * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _geo_is_plausible(city: str, lat: Any, lng: Any) -> bool:
    profile = CITY_PROFILES.get(city)
    if not profile or lat is None or lng is None:
        return bool(lat is not None and lng is not None)
    try:
        return _distance_km(float(lat), float(lng), float(profile["lat"]), float(profile["lng"])) <= 140
    except (TypeError, ValueError):
        return False


def _db_spot_to_agent_spot(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    geo_verified = bool(row.get("geo_verified")) and _geo_is_plausible(row["city"], row.get("latitude"), row.get("longitude"))
    return {
        "spot_id": row["id"],
        "name": row["name"],
        "city": row["city"],
        "lat": row.get("latitude"),
        "lng": row.get("longitude"),
        "spot_type": row.get("spot_type"),
        "location_hint": row.get("address") or raw.get("location_hint") or "",
        "source_types": ["internal_db", "postgresql"],
        "source_confidence": 0.92 if raw.get("confidence") == "high" else 0.78,
        "geo_verified": geo_verified,
        "suitable_styles": row.get("suitable_styles") or [],
        "visual_elements": row.get("visual_elements") or [],
        "themes": raw.get("themes") or row.get("visual_elements") or [],
        "best_time_hint": row.get("best_time_hint") or [],
        "weather_preference": row.get("weather_preference") or [],
        "ticket_required": bool(row.get("ticket_required")),
        "opening_hours": row.get("opening_hours"),
        "crowd_risk": row.get("crowd_risk"),
        "phone_friendly": bool(row.get("phone_friendly", True)),
        "base_photo_score": row.get("base_photo_score") or 7.8,
        "shooting_tips": row.get("shooting_tips") or [],
        "recommended_lens_or_focal_length": raw.get("recommended_lens_or_focal_length") or "手机 1x/2x",
        "access_and_notes": row.get("ticket_note") or raw.get("access_and_notes") or "",
        "source_urls": row.get("source_urls") or [],
    }


async def insert_travel_plan(pool: Any, plan: dict[str, Any]) -> None:
    parsed_goal = plan["parsed_goal"]
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO travel_plans (
              id, title, destination, departure_city, date_range, shooting_style,
              visual_elements, subject, platform, equipment, budget, status,
              user_input, reference_images, request_hash, parsed_goal, warnings, llm_used, created_at, updated_at
            )
            VALUES (
              $1::uuid, $2, $3, $4, $5::jsonb, $6::jsonb,
              $7::jsonb, $8::jsonb, $9::jsonb, $10::jsonb, $11, $12,
              $13, $14::jsonb, $15, $16::jsonb, $17::jsonb, $18, NOW(), NOW()
            )
            ON CONFLICT (id) DO UPDATE SET
              title = EXCLUDED.title,
              destination = EXCLUDED.destination,
              departure_city = EXCLUDED.departure_city,
              date_range = EXCLUDED.date_range,
              shooting_style = EXCLUDED.shooting_style,
              visual_elements = EXCLUDED.visual_elements,
              subject = EXCLUDED.subject,
              platform = EXCLUDED.platform,
              equipment = EXCLUDED.equipment,
              budget = EXCLUDED.budget,
              status = EXCLUDED.status,
              user_input = EXCLUDED.user_input,
              reference_images = EXCLUDED.reference_images,
              request_hash = EXCLUDED.request_hash,
              parsed_goal = EXCLUDED.parsed_goal,
              warnings = EXCLUDED.warnings,
              llm_used = EXCLUDED.llm_used,
              updated_at = NOW()
            """,
            plan["plan_id"],
            f"{parsed_goal.get('destination', '旅拍')}初始方案",
            parsed_goal.get("destination"),
            parsed_goal.get("departure_city"),
            _json_list(parsed_goal.get("date_range")),
            _json_list(parsed_goal.get("shooting_style")),
            _json_list(parsed_goal.get("visual_elements")),
            _json_list(parsed_goal.get("subject")),
            _json_list(parsed_goal.get("platform")),
            _json_list(parsed_goal.get("equipment")),
            _int_or_none(parsed_goal.get("budget")),
            plan["status"],
            plan["user_input"],
            _json_list(plan.get("reference_images")),
            plan.get("request_hash"),
            _json(parsed_goal),
            _json_list(plan.get("warnings")),
            bool(plan.get("llm_used")),
        )


async def update_travel_plan_result(pool: Any, plan: dict[str, Any]) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE travel_plans
            SET status = $2,
                destination = $3,
                departure_city = $4,
                date_range = $5::jsonb,
                shooting_style = $6::jsonb,
                visual_elements = $7::jsonb,
                subject = $8::jsonb,
                platform = $9::jsonb,
                equipment = $10::jsonb,
                budget = $11,
                parsed_goal = $12::jsonb,
                visual_goal = $13::jsonb,
                weather_context = $14::jsonb,
                sunlight_context = $15::jsonb,
                map_context = $16::jsonb,
                reference_context = $17::jsonb,
                discovery_context = $18::jsonb,
                image_analysis = $19::jsonb,
                repair_context = $20::jsonb,
                task_plan = $21::jsonb,
                agent_steps = $22::jsonb,
                backup_plan = $23::jsonb,
                final_markdown = $24,
                plan_json = $25::jsonb,
                warnings = $26::jsonb,
                llm_used = $27,
                execution_state = $28::jsonb,
                reference_images = $29::jsonb,
                request_hash = COALESCE($30, request_hash),
                updated_at = NOW()
            WHERE id = $1::uuid
            """,
            plan["plan_id"],
            plan["status"],
            plan["parsed_goal"].get("destination"),
            plan["parsed_goal"].get("departure_city"),
            _json_list(plan["parsed_goal"].get("date_range")),
            _json_list(plan["parsed_goal"].get("shooting_style")),
            _json_list(plan["parsed_goal"].get("visual_elements")),
            _json_list(plan["parsed_goal"].get("subject")),
            _json_list(plan["parsed_goal"].get("platform")),
            _json_list(plan["parsed_goal"].get("equipment")),
            _int_or_none(plan["parsed_goal"].get("budget")),
            _json(plan["parsed_goal"]),
            _json(plan.get("visual_goal")),
            _json(plan.get("weather_context")),
            _json(plan.get("sunlight_context")),
            _json(plan.get("map_context")),
            _json(plan.get("reference_context")),
            _json(plan.get("discovery_context")),
            _json(plan.get("image_analysis")),
            _json(plan.get("repair_context")),
            _json_list(plan.get("task_plan")),
            _json_list(plan.get("agent_steps")),
            _json_list(plan.get("backup_plan")),
            plan.get("final_markdown"),
            _json(plan),
            _json_list(plan.get("warnings")),
            bool(plan.get("llm_used")),
            _json(plan.get("execution_state")) if plan.get("execution_state") is not None else None,
            _json_list(plan.get("reference_images")),
            plan.get("request_hash"),
        )


async def replace_spot_time_options(pool: Any, plan_id: str, options: list[dict[str, Any]]) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM spot_time_options WHERE plan_id = $1::uuid", plan_id)
            for option in options:
                await conn.execute(
                    """
                    INSERT INTO spot_time_options (
                      plan_id, option_id, spot_id, spot_name, date, time_window, start_time, end_time,
                      slot_type, light_label, shoot_goal, expected_visual, style_fit, visual_element_fit,
                      light_fit, weather_fit, transport_fit, risk_score, ticket_fit, constraint_fit,
                      final_score, risks, recommended_shots, data
                    )
                    VALUES (
                      $1::uuid, $2, $3, $4, $5, $6, $7, $8,
                      $9, $10, $11, $12::jsonb, $13, $14,
                      $15, $16, $17, $18, $19, $20,
                      $21, $22::jsonb, $23::jsonb, $24::jsonb
                    )
                    """,
                    plan_id,
                    option.get("option_id"),
                    option.get("spot_id"),
                    option.get("spot_name"),
                    option.get("date"),
                    option.get("time_window"),
                    option.get("start_time"),
                    option.get("end_time"),
                    option.get("slot_type"),
                    option.get("light_label"),
                    option.get("shoot_goal"),
                    _json_list(option.get("expected_visual")),
                    option.get("style_fit"),
                    option.get("visual_element_fit"),
                    option.get("light_fit"),
                    option.get("weather_fit"),
                    option.get("transport_fit"),
                    option.get("risk_score"),
                    option.get("ticket_fit"),
                    option.get("constraint_fit"),
                    option.get("final_score"),
                    _json_list(option.get("risks")),
                    _json_list(option.get("recommended_shots")),
                    _json(option),
                )


async def replace_plan_route_items(pool: Any, plan_id: str, route: list[dict[str, Any]]) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM plan_route_items WHERE plan_id = $1::uuid", plan_id)
            for item in route:
                await conn.execute(
                    """
                    INSERT INTO plan_route_items (
                      plan_id, option_id, sequence, date, start_time, end_time, item_type,
                      spot_name, shoot_goal, route_note, guide, completed, skipped, data
                    )
                    VALUES (
                      $1::uuid, $2, $3, $4, $5, $6, $7,
                      $8, $9, $10, $11::jsonb, $12, $13, $14::jsonb
                    )
                    """,
                    plan_id,
                    item.get("option_id"),
                    item.get("sequence"),
                    item.get("date"),
                    item.get("start_time"),
                    item.get("end_time"),
                    "shoot",
                    item.get("spot_name"),
                    item.get("shoot_goal"),
                    item.get("route_note"),
                    _json(item.get("guide")),
                    bool(item.get("completed", False)),
                    bool(item.get("skipped", False)),
                    _json(item),
                )


async def replace_agent_steps(pool: Any, plan_id: str, steps: list[dict[str, Any]]) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM agent_steps WHERE plan_id = $1::uuid", plan_id)
            for index, step in enumerate(steps, start=1):
                tool_output = step.get("tool_output")
                if isinstance(tool_output, dict):
                    success = tool_output.get("success")
                    source = tool_output.get("source")
                else:
                    success = None
                    source = None
                await conn.execute(
                    """
                    INSERT INTO agent_steps (
                      plan_id, step_index, task_id, step_type, reasoning_summary,
                      tool_name, tool_input, tool_output, observation, duration_ms, success, source
                    )
                    VALUES (
                      $1::uuid, $2, $3, $4, $5,
                      $6, $7::jsonb, $8::jsonb, $9::jsonb, $10, $11, $12
                    )
                    """,
                    plan_id,
                    index,
                    step.get("task_id"),
                    step.get("step_type"),
                    step.get("reasoning_summary"),
                    step.get("tool_name"),
                    _json(step.get("tool_input")),
                    _json(tool_output),
                    _json(step.get("observation")),
                    _int_or_none(step.get("duration_ms")),
                    success if isinstance(success, bool) else None,
                    source,
                )


async def get_travel_plan(pool: Any, plan_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM travel_plans WHERE id = $1::uuid", plan_id)
        if row is None:
            return None
        plan = _db_plan_to_response(_record_to_dict(row))
        option_rows = await conn.fetch(
            "SELECT data FROM spot_time_options WHERE plan_id = $1::uuid ORDER BY final_score DESC NULLS LAST",
            plan_id,
        )
        route_rows = await conn.fetch(
            "SELECT data FROM plan_route_items WHERE plan_id = $1::uuid ORDER BY sequence",
            plan_id,
        )
        step_rows = await conn.fetch(
            """
            SELECT *
            FROM agent_steps
            WHERE plan_id = $1::uuid
            ORDER BY step_index
            """,
            plan_id,
        )
    plan["spot_time_options"] = [_maybe_json(row["data"]) for row in option_rows]
    plan["route"] = [_maybe_json(row["data"]) for row in route_rows]
    if step_rows:
        plan["agent_steps"] = [_agent_step_to_response(_record_to_dict(row)) for row in step_rows]
    return plan


async def get_cached_completed_plan(pool: Any, request_hash: str, exclude_plan_id: str, ttl_seconds: int) -> dict[str, Any] | None:
    if not request_hash or ttl_seconds <= 0:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id
            FROM travel_plans
            WHERE request_hash = $1
              AND id <> $2::uuid
              AND status IN ('completed', 'cannot_satisfy')
              AND final_markdown IS NOT NULL
              AND updated_at >= NOW() - make_interval(secs => $3)
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            request_hash,
            exclude_plan_id,
            max(1, ttl_seconds),
        )
    if row is None:
        return None
    return await get_travel_plan(pool, str(row["id"]))


async def list_travel_plans(pool: Any, limit: int = 30) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
              id, status, user_input, destination, date_range, parsed_goal,
              warnings, llm_used, final_markdown, created_at, updated_at
            FROM travel_plans
            ORDER BY updated_at DESC NULLS LAST, created_at DESC
            LIMIT $1
            """,
            max(1, min(limit, 100)),
        )
    return [_db_plan_summary_to_response(_record_to_dict(row)) for row in rows]


async def cleanup_expired_travel_plans(pool: Any, retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM travel_plans
            WHERE updated_at < NOW() - make_interval(days => $1)
            """,
            retention_days,
        )
    try:
        return int(result.rsplit(" ", 1)[-1])
    except (IndexError, ValueError):
        return 0


async def delete_travel_plan(pool: Any, plan_id: str) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM travel_plans WHERE id = $1::uuid", plan_id)
    return result.endswith("1")


async def try_mark_plan_generating(pool: Any, plan_id: str) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE travel_plans
            SET status = 'generating',
                updated_at = NOW()
            WHERE id = $1::uuid
              AND status NOT IN ('generating')
            """,
            plan_id,
        )
    return result.endswith("1")


async def update_plan_status(pool: Any, plan_id: str, status: str, warnings: list[str] | None = None) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE travel_plans
            SET status = $2,
                warnings = COALESCE($3::jsonb, warnings),
                updated_at = NOW()
            WHERE id = $1::uuid
            """,
            plan_id,
            status,
            _json_list(warnings) if warnings is not None else None,
        )


async def touch_travel_plan(pool: Any, plan_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE travel_plans
            SET updated_at = NOW()
            WHERE id = $1::uuid
            """,
            plan_id,
        )


async def insert_plan_message(pool: Any, message: dict[str, Any]) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO plan_messages (
              plan_id, role, content, reference_images, tool_requests,
              tool_results, response, warnings, created_at
            )
            VALUES ($1::uuid, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7::jsonb, $8::jsonb, NOW())
            RETURNING *
            """,
            message["plan_id"],
            message.get("role") or "user",
            message.get("content") or "",
            _json_list(message.get("reference_images")),
            _json_list(message.get("tool_requests")),
            _json_list(message.get("tool_results")),
            _json(message.get("response")),
            _json_list(message.get("warnings")),
        )
    return _plan_message_to_response(_record_to_dict(row))


async def list_plan_messages(pool: Any, plan_id: str) -> list[dict[str, Any]] | None:
    plan = await get_travel_plan(pool, plan_id)
    if plan is None:
        return None
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT *
            FROM plan_messages
            WHERE plan_id = $1::uuid
            ORDER BY created_at
            """,
            plan_id,
        )
    return [_plan_message_to_response(_record_to_dict(row)) for row in rows]


async def update_plan_execution_state(
    pool: Any,
    plan_id: str,
    execution_state: dict[str, Any],
    status: str | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE travel_plans
            SET execution_state = $2::jsonb,
                status = COALESCE($3, status),
                updated_at = NOW()
            WHERE id = $1::uuid
            """,
            plan_id,
            _json(execution_state),
            status,
        )


async def list_plan_options(pool: Any, plan_id: str) -> list[dict[str, Any]] | None:
    plan = await get_travel_plan(pool, plan_id)
    if plan is None:
        return None
    return plan.get("spot_time_options", [])


async def list_plan_route(pool: Any, plan_id: str) -> list[dict[str, Any]] | None:
    plan = await get_travel_plan(pool, plan_id)
    if plan is None:
        return None
    return plan.get("route", [])


def _db_plan_to_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_id": str(row["id"]),
        "status": row.get("status"),
        "user_input": row.get("user_input"),
        "reference_images": row.get("reference_images") or [],
        "parsed_goal": row.get("parsed_goal") or {},
        "visual_goal": row.get("visual_goal") or {},
        "weather_context": row.get("weather_context") or {},
        "sunlight_context": row.get("sunlight_context") or {},
        "map_context": row.get("map_context") or {},
        "reference_context": row.get("reference_context") or {},
        "discovery_context": row.get("discovery_context") or {},
        "image_analysis": row.get("image_analysis") or {},
        "repair_context": row.get("repair_context") or {},
        "task_plan": row.get("task_plan") or [],
        "agent_steps": row.get("agent_steps") or [],
        "final_markdown": row.get("final_markdown"),
        "route": [],
        "spot_time_options": [],
        "backup_plan": row.get("backup_plan") or [],
        "warnings": row.get("warnings") or [],
        "llm_used": bool(row.get("llm_used")),
        "execution_state": row.get("execution_state"),
        "request_hash": row.get("request_hash"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _db_plan_summary_to_response(row: dict[str, Any]) -> dict[str, Any]:
    parsed_goal = row.get("parsed_goal") or {}
    date_range = row.get("date_range") or parsed_goal.get("date_range") or []
    return {
        "plan_id": str(row["id"]),
        "status": row.get("status"),
        "user_input": row.get("user_input") or "",
        "destination": row.get("destination") or parsed_goal.get("destination"),
        "date_range": date_range,
        "warnings": row.get("warnings") or [],
        "llm_used": bool(row.get("llm_used")),
        "final_markdown": row.get("final_markdown"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _plan_message_to_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "plan_id": str(row["plan_id"]),
        "role": row.get("role"),
        "content": row.get("content") or "",
        "reference_images": row.get("reference_images") or [],
        "tool_requests": row.get("tool_requests") or [],
        "tool_results": row.get("tool_results") or [],
        "response": row.get("response") or {},
        "warnings": row.get("warnings") or [],
        "created_at": row.get("created_at"),
    }


def _agent_step_to_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": row.get("task_id"),
        "step_type": row.get("step_type"),
        "reasoning_summary": row.get("reasoning_summary"),
        "tool_name": row.get("tool_name"),
        "tool_input": row.get("tool_input") or {},
        "tool_output": row.get("tool_output") or {},
        "observation": row.get("observation") or {},
        "duration_ms": row.get("duration_ms"),
    }
