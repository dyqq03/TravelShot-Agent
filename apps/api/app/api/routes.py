from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.schemas.plans import (
    ExecutionAdjustRequest,
    ExecutionStateRequest,
    FollowUpRequest,
    FollowUpResponse,
    LiveStartResponse,
    PlanCreateRequest,
    PlanCreateResponse,
    PlanGenerateResponse,
    PlanResponse,
    PlanSummary,
)
from app.services.plan_service import (
    PlanGenerationBlocked,
    adjust_plan,
    create_plan,
    delete_plan,
    follow_up_plan,
    generate_plan,
    get_plan,
    list_messages,
    list_plans,
    list_route,
    list_spot_time_options,
    start_live_mode,
    update_execution_state,
)

router = APIRouter()


@router.post("/plans", response_model=PlanCreateResponse, tags=["plans"])
async def create_travel_plan(payload: PlanCreateRequest) -> PlanCreateResponse:
    return await create_plan(payload)


@router.get("/plans", response_model=list[PlanSummary], tags=["plans"])
async def list_travel_plans() -> list[PlanSummary]:
    return await list_plans()


@router.post("/plans/{plan_id}/generate", response_model=PlanGenerateResponse, tags=["plans"])
async def generate_travel_plan(plan_id: UUID) -> PlanGenerateResponse:
    try:
        plan = await generate_plan(str(plan_id))
    except PlanGenerationBlocked as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


@router.delete("/plans/{plan_id}", tags=["plans"])
async def delete_travel_plan(plan_id: UUID) -> dict:
    deleted = await delete_plan(str(plan_id))
    if not deleted:
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"deleted": True}


@router.get("/plans/{plan_id}", response_model=PlanResponse, tags=["plans"])
async def get_travel_plan(plan_id: UUID) -> PlanResponse:
    plan = await get_plan(str(plan_id))
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


@router.post("/plans/{plan_id}/followups", response_model=FollowUpResponse, tags=["plans"])
async def follow_up_travel_plan(plan_id: UUID, payload: FollowUpRequest) -> FollowUpResponse:
    try:
        result = await follow_up_plan(str(plan_id), payload)
    except PlanGenerationBlocked as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return result


@router.get("/plans/{plan_id}/messages", tags=["plans"])
async def get_plan_messages(plan_id: UUID) -> list[dict]:
    messages = await list_messages(str(plan_id))
    if messages is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return messages


@router.get("/plans/{plan_id}/spot-time-options", tags=["plans"])
async def get_spot_time_options(plan_id: UUID) -> list[dict]:
    options = await list_spot_time_options(str(plan_id))
    if options is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return options


@router.get("/plans/{plan_id}/route", tags=["plans"])
async def get_route(plan_id: UUID) -> list[dict]:
    route = await list_route(str(plan_id))
    if route is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return route


@router.post("/plans/{plan_id}/live/start", response_model=LiveStartResponse, tags=["live"])
async def start_plan_live_mode(plan_id: UUID) -> LiveStartResponse:
    state = await start_live_mode(str(plan_id))
    if state is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return state


@router.patch("/plans/{plan_id}/execution-state", tags=["live"])
async def patch_execution_state(plan_id: UUID, payload: ExecutionStateRequest) -> dict:
    state = await update_execution_state(str(plan_id), payload)
    if state is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return state


@router.post("/plans/{plan_id}/adjust", tags=["live"])
async def adjust_travel_plan(plan_id: UUID, payload: ExecutionAdjustRequest) -> dict:
    try:
        result = await adjust_plan(str(plan_id), payload)
    except PlanGenerationBlocked as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return result
