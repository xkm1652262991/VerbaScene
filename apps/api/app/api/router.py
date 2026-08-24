from fastapi import APIRouter

from app.api.routes.agent_configs import router as agent_configs_router
from app.api.routes.asset_candidates import router as asset_candidates_router
from app.api.routes.assets import router as assets_router
from app.api.routes.dialogues import router as dialogues_router
from app.api.routes.entities import router as entities_router
from app.api.routes.exports import router as exports_router
from app.api.routes.health import router as health_router
from app.api.routes.projects import router as projects_router
from app.api.routes.providers import router as providers_router
from app.api.routes.quality import router as quality_router
from app.api.routes.readiness import router as readiness_router
from app.api.routes.shots import router as shots_router
from app.api.routes.shot_frames import router as shot_frames_router
from app.api.routes.scripts import router as scripts_router
from app.api.routes.tasks import router as tasks_router
from app.api.routes.workflow import router as workflow_router

api_router = APIRouter()
api_router.include_router(agent_configs_router)
api_router.include_router(asset_candidates_router)
api_router.include_router(assets_router)
api_router.include_router(dialogues_router)
api_router.include_router(entities_router)
api_router.include_router(exports_router)
api_router.include_router(health_router)
api_router.include_router(projects_router)
api_router.include_router(providers_router)
api_router.include_router(quality_router)
api_router.include_router(readiness_router)
api_router.include_router(shots_router)
api_router.include_router(shot_frames_router)
api_router.include_router(scripts_router)
api_router.include_router(tasks_router)
api_router.include_router(workflow_router)
