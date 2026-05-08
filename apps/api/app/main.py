from contextlib import asynccontextmanager
from secrets import compare_digest
from time import monotonic

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router as api_router
from app.core.config import settings
from app.db.postgres import close_postgres, connect_postgres, init_schema
from app.db.repository import cleanup_expired_travel_plans, upsert_photo_spots
from app.db.runtime import check_runtime_services
from app.spot.repository import load_spots


_RATE_LIMIT_WINDOW_SECONDS = 60.0
_request_counts: dict[str, tuple[float, int]] = {}


def _rate_limit_exceeded(request: Request) -> bool:
    limit = settings.api_rate_limit_per_minute
    if limit <= 0:
        return False
    now = monotonic()
    client_host = request.client.host if request.client else "unknown"
    reset_at, count = _request_counts.get(client_host, (now + _RATE_LIMIT_WINDOW_SECONDS, 0))
    if reset_at <= now:
        reset_at = now + _RATE_LIMIT_WINDOW_SECONDS
        count = 0
    count += 1
    _request_counts[client_host] = (reset_at, count)
    return count > limit


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.require_runtime_services:
        app.state.runtime_checks = [item.as_dict() for item in check_runtime_services(settings)]
    pool = await connect_postgres()
    await init_schema(pool)
    app.state.expired_plan_count = await cleanup_expired_travel_plans(pool, settings.history_retention_days)
    if settings.import_seed_spots_on_startup:
        app.state.seed_spot_count = await upsert_photo_spots(pool, list(load_spots()))
    try:
        yield
    finally:
        await close_postgres()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="LLM-led API for TravelShot Agent.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def require_api_access_token(request: Request, call_next):
        if request.url.path.startswith("/api") and request.method != "OPTIONS":
            token = settings.api_access_token
            if token:
                supplied = request.headers.get("x-api-token", "")
                if not compare_digest(supplied, token):
                    return JSONResponse(status_code=401, content={"detail": "Invalid or missing API access token."})
            if _rate_limit_exceeded(request):
                return JSONResponse(status_code=429, content={"detail": "Too many API requests. Please retry later."})
        return await call_next(request)

    @app.get("/health", tags=["system"])
    async def health() -> dict:
        checks = []
        if settings.require_runtime_services:
            checks = [item.as_dict() for item in check_runtime_services(settings, raise_on_error=False)]
            failed = [item for item in checks if not item["ok"]]
            if failed:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "status": "error",
                        "message": "PostgreSQL or Redis is not reachable.",
                        "dependencies": checks,
                    },
                )
        return {
            "status": "ok",
            "service": settings.app_name,
            "environment": settings.app_env,
            "phase": "phase4_5",
            "dependencies": checks,
        }

    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
