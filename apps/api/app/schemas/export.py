from datetime import datetime
from decimal import Decimal

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.asset import AssetRead


class ExportRead(BaseModel):
    id: str
    project_id: str
    asset_id: str | None
    resolution: str
    duration_sec: Decimal | None
    format: str
    subtitle_mode: str
    ffmpeg_command: str | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CompositionResponse(BaseModel):
    export: ExportRead
    asset: AssetRead


class CompositionRequest(BaseModel):
    subtitle_mode: Literal["none", "en", "bilingual"] = "none"
