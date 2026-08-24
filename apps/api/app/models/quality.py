from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSON_DOCUMENT
from app.models.base import IdMixin, TimestampMixin


class QualityCheck(IdMixin, TimestampMixin, Base):
    __tablename__ = "quality_checks"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_id: Mapped[str | None] = mapped_column(String(36), index=True)
    shot_id: Mapped[str | None] = mapped_column(String(36), index=True)
    asset_id: Mapped[str | None] = mapped_column(String(36), index=True)
    method: Mapped[str] = mapped_column(String(40), default="rule", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), default="pass", nullable=False, index=True)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=100, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    issues: Mapped[list] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    suggestions: Mapped[list] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    raw_response: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
