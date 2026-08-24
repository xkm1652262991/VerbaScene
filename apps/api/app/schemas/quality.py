from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class QualityCheckRead(BaseModel):
    id: str
    project_id: str
    stage: str
    target_type: str
    target_id: str | None
    shot_id: str | None
    asset_id: str | None
    method: str
    status: str
    score: Decimal
    summary: str
    issues: list[dict[str, Any]]
    suggestions: list[str]
    raw_response: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class QualityCheckRunResponse(BaseModel):
    checks: list[QualityCheckRead]
