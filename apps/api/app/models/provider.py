from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSON_DOCUMENT
from app.models.base import IdMixin, TimestampMixin


class ProviderConfig(IdMixin, TimestampMixin, Base):
    __tablename__ = "provider_configs"

    workspace_id: Mapped[str | None] = mapped_column(String(36), index=True)
    provider_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(160), nullable=False)
    api_key_ref: Mapped[str | None] = mapped_column(String(255))
    base_url: Mapped[str | None] = mapped_column(Text)
    default_params: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
