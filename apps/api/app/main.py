from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.openapi import install_openapi_contract
from app.db import SessionLocal
from app.services.provider_config_service import reload_runtime_provider_configs
from app.services.script_generation_queue_service import start_script_generation_queue
from app.services.video_generation_queue_service import start_video_generation_queue


@asynccontextmanager
async def lifespan(_app: FastAPI):
    with SessionLocal() as db:
        reload_runtime_provider_configs(db)
    start_script_generation_queue()
    start_video_generation_queue()
    yield


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )

    register_exception_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    install_openapi_contract(app)
    storage_root = Path(settings.storage_root).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    app.mount("/storage", StaticFiles(directory=storage_root), name="storage")

    return app


app = create_app()
