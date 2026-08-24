from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Shot, ShotFrameImage, ShotFramePrompt


def list_shot_frame_prompts(
    db: Session,
    project_id: str,
    *,
    shot_id: str | None = None,
    frame_type: str | None = None,
) -> list[ShotFramePrompt]:
    """Read-only access to historical frame prompts."""
    statement = select(ShotFramePrompt).where(ShotFramePrompt.project_id == project_id)
    if shot_id:
        statement = statement.where(ShotFramePrompt.shot_id == shot_id)
    if frame_type:
        statement = statement.where(ShotFramePrompt.frame_type == frame_type)
    return list(db.scalars(statement.order_by(ShotFramePrompt.version.desc())).all())


def list_shot_frame_images(
    db: Session,
    project_id: str,
    *,
    shot_id: str | None = None,
    frame_type: str | None = None,
) -> list[ShotFrameImage]:
    """Read-only access to historical frame images."""
    return list(
        db.scalars(
            _shot_frame_image_statement(db, project_id, shot_id=shot_id, frame_type=frame_type)
            .order_by(ShotFrameImage.shot_id, ShotFrameImage.frame_type, ShotFrameImage.version.desc())
        ).all()
    )


def list_shot_frame_images_page(
    db: Session,
    project_id: str,
    *,
    shot_id: str | None = None,
    frame_type: str | None = None,
    offset: int = 0,
    limit: int = 500,
) -> tuple[list[ShotFrameImage], int]:
    """Read-only paginated access to historical frame images."""
    statement = _shot_frame_image_statement(db, project_id, shot_id=shot_id, frame_type=frame_type)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = list(
        db.scalars(
            statement
            .order_by(ShotFrameImage.shot_id, ShotFrameImage.frame_type, ShotFrameImage.version.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return rows, total


def _current_shot_ids(db: Session, project_id: str) -> list[str]:
    return list(
        db.scalars(
            select(Shot.id)
            .where(Shot.project_id == project_id)
            .where(Shot.is_current.is_(True))
        ).all()
    )


def _shot_frame_image_statement(
    db: Session,
    project_id: str,
    *,
    shot_id: str | None,
    frame_type: str | None,
):
    statement = select(ShotFrameImage).where(ShotFrameImage.project_id == project_id)
    statement = (
        statement.where(ShotFrameImage.shot_id == shot_id)
        if shot_id
        else statement.where(ShotFrameImage.shot_id.in_(_current_shot_ids(db, project_id)))
    )
    return statement.where(ShotFrameImage.frame_type == frame_type) if frame_type else statement
