from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db.runtime import RuntimeDependencyError


_pool: Any | None = None


def _schema_path() -> Path:
    for base in [Path.cwd(), *Path(__file__).resolve().parents]:
        candidate = base / "db" / "schema.sql"
        if candidate.exists():
            return candidate
    raise RuntimeDependencyError("Cannot find db/schema.sql for PostgreSQL initialization.")


async def connect_postgres() -> Any:
    global _pool
    if _pool is not None:
        return _pool

    try:
        import asyncpg
    except ImportError as exc:
        raise RuntimeDependencyError(
            "asyncpg is required for PostgreSQL persistence. Run `pip install -e apps/api`."
        ) from exc

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
    return _pool


async def close_postgres() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> Any:
    if _pool is None:
        raise RuntimeDependencyError("PostgreSQL pool is not initialized.")
    return _pool


async def init_schema(pool: Any) -> None:
    schema = _schema_path().read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        await conn.execute(schema)
