from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.common import ApiResponse, PageResponse
from app.schemas.dialogue import DialogueRead, DialogueSaveRequest, DialogueSaveResponse
from app.services.dialogue_service import list_dialogues_page, save_dialogues


router = APIRouter(tags=["dialogues"])


@router.get("/api/projects/{project_id}/dialogues", response_model=PageResponse[DialogueRead])
def list_project_dialogues_endpoint(
    project_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> PageResponse[DialogueRead]:
    dialogues, total = list_dialogues_page(
        db,
        project_id,
        offset=offset,
        limit=limit,
    )
    return PageResponse(
        items=dialogues,
        meta={"total": total, "offset": offset, "limit": limit},
    )


@router.put(
    "/api/projects/{project_id}/dialogues",
    response_model=ApiResponse[DialogueSaveResponse],
)
def save_project_dialogues_endpoint(
    project_id: str,
    payload: DialogueSaveRequest,
    db: Session = Depends(get_db),
) -> ApiResponse[DialogueSaveResponse]:
    dialogues, stale_shot_ids = save_dialogues(db, project_id, payload)
    return ApiResponse(
        data={
            "dialogues": dialogues,
            "stale_shot_ids": stale_shot_ids,
            "metadata": {"dialogue_language": "en"},
        }
    )
