"""Health and readiness endpoints."""

from __future__ import annotations

import time

from fastapi import APIRouter, status

from app import __version__
from app.api.dependencies import DatabaseDep, RedisDep
from app.schemas import HealthResponse, HealthStatus

router = APIRouter(tags=["system"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness + dependency healthcheck",
)
async def health(database: DatabaseDep, redis: RedisDep) -> HealthResponse:
    checks: list[HealthStatus] = []

    t0 = time.perf_counter()
    db_ok = await database.healthcheck()
    checks.append(
        HealthStatus(
            name="database",
            healthy=db_ok,
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
    )

    t0 = time.perf_counter()
    redis_ok = await redis.healthcheck()
    checks.append(
        HealthStatus(
            name="redis",
            healthy=redis_ok,
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
    )

    overall = "ok" if all(c.healthy for c in checks) else "degraded"
    return HealthResponse(status=overall, version=__version__, checks=checks)


@router.get(
    "/health/live",
    summary="Liveness probe (no dependencies)",
    status_code=status.HTTP_200_OK,
)
async def live() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/health/ready", summary="Readiness probe")
async def ready(database: DatabaseDep, redis: RedisDep) -> dict[str, object]:
    db_ok = await database.healthcheck()
    redis_ok = await redis.healthcheck()
    return {
        "ready": db_ok and redis_ok,
        "database": db_ok,
        "redis": redis_ok,
    }
