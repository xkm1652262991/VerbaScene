from typing import Any

from fastapi import APIRouter

from app.core.config import settings
from app.core.redis import check_redis
from app.db.health import check_database

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, Any]:
    database_status = _dependency_status(check_database)
    database_status["required"] = True
    redis_status = _dependency_status(check_redis)
    redis_status["required"] = settings.redis_required
    dependencies = {
        "database": database_status,
        "redis": redis_status,
    }
    is_healthy = database_status["ok"] and (redis_status["ok"] or not settings.redis_required)

    return {
        "status": "ok" if is_healthy else "degraded",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
        "persistence_mode": settings.persistence_mode,
        "dependencies": dependencies,
    }


def _dependency_status(checker: Any) -> dict[str, Any]:
    try:
        checker()
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
        }

    return {
        "ok": True,
        "error": None,
    }
