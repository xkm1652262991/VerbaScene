from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSON_DOCUMENT
from app.models.base import IdMixin, TimestampMixin


class ProjectStageRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "project_stage_runs"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(80), default="generate", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), default="running", nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), index=True)
    input_payload: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    output_payload: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_metadata: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
