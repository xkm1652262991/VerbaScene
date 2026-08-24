from datetime import datetime
from decimal import Decimal
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

class DialogueRead(BaseModel):
    id: str
    project_id: str
    script_id: str | None
    character_id: str | None
    shot_id: str | None
    speaker_name: str
    text: str
    translation_zh: str | None
    emotion: str | None
    sequence_order: int
    beat_id: str | None
    sound_cues: list[str]
    start_time: Decimal | None
    end_time: Decimal | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DialogueInput(BaseModel):
    id: str | None = None
    shot_id: str | None = None
    character_id: str | None = None
    speaker_name: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=2000)
    translation_zh: str | None = Field(default=None, max_length=2000)
    emotion: str | None = Field(default=None, max_length=80)
    sequence_order: int = Field(ge=0)
    beat_id: str | None = Field(default=None, max_length=80)
    sound_cues: list[str] = Field(default_factory=list, max_length=20)
    start_time: Decimal | None = Field(default=None, ge=0)
    end_time: Decimal | None = Field(default=None, ge=0)

    @field_validator("speaker_name", "text", "translation_zh", "emotion", "beat_id", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("text")
    @classmethod
    def require_english_dialogue(cls, value: str) -> str:
        if not re.search(r"[A-Za-z]", value):
            raise ValueError("英文对白必须包含英文字母")
        return value


class DialogueSaveRequest(BaseModel):
    dialogues: list[DialogueInput] = Field(default_factory=list, max_length=500)


class DialogueSaveResponse(BaseModel):
    dialogues: list[DialogueRead]
    stale_shot_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
