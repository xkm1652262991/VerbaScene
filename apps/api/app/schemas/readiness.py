from pydantic import BaseModel, Field


class ReadinessCheckRead(BaseModel):
    key: str
    label: str
    ok: bool
    message: str
    severity: str = "blocker"


class ShotReadinessRead(BaseModel):
    shot_id: str
    shot_no: int
    ready_for_image: bool
    ready_for_video: bool
    checks: list[ReadinessCheckRead] = Field(default_factory=list)
    image_blockers: list[str] = Field(default_factory=list)
    video_blockers: list[str] = Field(default_factory=list)


class ProjectReadinessSummaryRead(BaseModel):
    shot_count: int
    image_ready_count: int
    video_ready_count: int
    image_blocker_count: int
    video_blocker_count: int


class ProjectReadinessRead(BaseModel):
    project_id: str
    summary: ProjectReadinessSummaryRead
    shots: list[ShotReadinessRead] = Field(default_factory=list)
