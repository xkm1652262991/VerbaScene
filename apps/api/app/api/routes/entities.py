from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.common import ApiResponse, PageResponse
from app.schemas.entity import (
    CharacterRead,
    CharacterUpdate,
    EntityBundleRead,
    EntityGenerationResponse,
    PropRead,
    PropUpdate,
    SceneRead,
    SceneUpdate,
)
from app.services.entity_service import (
    generate_entities,
    list_characters,
    list_characters_page,
    list_props,
    list_props_page,
    list_scenes,
    list_scenes_page,
    update_character,
    update_prop,
    update_scene,
)

router = APIRouter(tags=["entities"])


@router.post("/api/projects/{project_id}/entities/generate", response_model=ApiResponse[EntityGenerationResponse])
def generate_project_entities_endpoint(
    project_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[EntityGenerationResponse]:
    characters, scenes, props, task = generate_entities(db, project_id)
    return ApiResponse(
        data={
            "characters": characters,
            "scenes": scenes,
            "props": props,
            "task_id": task.id,
        }
    )


@router.get("/api/projects/{project_id}/entities", response_model=ApiResponse[EntityBundleRead])
def get_project_entities_endpoint(
    project_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[EntityBundleRead]:
    return ApiResponse(
        data={
            "characters": list_characters(db, project_id),
            "scenes": list_scenes(db, project_id),
            "props": list_props(db, project_id),
        }
    )


@router.get("/api/projects/{project_id}/characters", response_model=PageResponse[CharacterRead])
def list_project_characters_endpoint(
    project_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> PageResponse[CharacterRead]:
    characters, total = list_characters_page(db, project_id, offset=offset, limit=limit)
    return PageResponse(items=characters, meta={"total": total, "offset": offset, "limit": limit})


@router.patch("/api/characters/{character_id}", response_model=ApiResponse[CharacterRead])
def update_character_endpoint(
    character_id: str,
    payload: CharacterUpdate,
    db: Session = Depends(get_db),
) -> ApiResponse[CharacterRead]:
    return ApiResponse(data=update_character(db, character_id, payload))


@router.get("/api/projects/{project_id}/scenes", response_model=PageResponse[SceneRead])
def list_project_scenes_endpoint(
    project_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> PageResponse[SceneRead]:
    scenes, total = list_scenes_page(db, project_id, offset=offset, limit=limit)
    return PageResponse(items=scenes, meta={"total": total, "offset": offset, "limit": limit})


@router.patch("/api/scenes/{scene_id}", response_model=ApiResponse[SceneRead])
def update_scene_endpoint(
    scene_id: str,
    payload: SceneUpdate,
    db: Session = Depends(get_db),
) -> ApiResponse[SceneRead]:
    return ApiResponse(data=update_scene(db, scene_id, payload))


@router.get("/api/projects/{project_id}/props", response_model=PageResponse[PropRead])
def list_project_props_endpoint(
    project_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> PageResponse[PropRead]:
    props, total = list_props_page(db, project_id, offset=offset, limit=limit)
    return PageResponse(items=props, meta={"total": total, "offset": offset, "limit": limit})


@router.patch("/api/props/{prop_id}", response_model=ApiResponse[PropRead])
def update_prop_endpoint(
    prop_id: str,
    payload: PropUpdate,
    db: Session = Depends(get_db),
) -> ApiResponse[PropRead]:
    return ApiResponse(data=update_prop(db, prop_id, payload))
