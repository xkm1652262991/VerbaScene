from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship, validates

from app.db.base import Base
from app.db.json_payload import compact_json_payload
from app.db.types import JSON_DOCUMENT
from app.models.base import IdMixin, TimestampMixin


class Asset(IdMixin, TimestampMixin, Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("completion_key", name="uq_assets_completion_key"),
    )

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    asset_role: Mapped[str | None] = mapped_column(String(80), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(80), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), index=True)
    variant_key: Mapped[str | None] = mapped_column(String(120), index=True)
    source_task_id: Mapped[str | None] = mapped_column(String(36), index=True)
    completion_key: Mapped[str | None] = mapped_column(String(160))
    source_stage_run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    source_script_id: Mapped[str | None] = mapped_column(String(36), index=True)
    source_shot_batch_id: Mapped[str | None] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(120))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_sec: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    provider: Mapped[str | None] = mapped_column(String(120), index=True)
    model: Mapped[str | None] = mapped_column(String(160))
    prompt: Mapped[str | None] = mapped_column(Text)
    negative_prompt: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False, index=True)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    export: Mapped["Export | None"] = relationship(back_populates="asset")


class AssetCandidate(IdMixin, TimestampMixin, Base):
    __tablename__ = "asset_candidates"
    __table_args__ = (
        UniqueConstraint(
            "completion_key",
            name="uq_asset_candidates_completion_key",
        ),
    )

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("assets.id", ondelete="SET NULL"),
        index=True,
    )
    source_task_id: Mapped[str | None] = mapped_column(String(36), index=True)
    completion_key: Mapped[str | None] = mapped_column(String(160))
    source_stage_run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    source_script_id: Mapped[str | None] = mapped_column(String(36), index=True)
    source_shot_batch_id: Mapped[str | None] = mapped_column(String(36), index=True)
    candidate_type: Mapped[str] = mapped_column(String(40), default="generated", nullable=False, index=True)
    asset_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    asset_role: Mapped[str | None] = mapped_column(String(80), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(80), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), index=True)
    variant_key: Mapped[str | None] = mapped_column(String(120), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(120))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_sec: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    provider: Mapped[str | None] = mapped_column(String(120), index=True)
    model: Mapped[str | None] = mapped_column(String(160))
    prompt: Mapped[str | None] = mapped_column(Text)
    negative_prompt: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending_review", nullable=False, index=True)
    review_note: Mapped[str | None] = mapped_column(Text)
    promoted_asset_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("assets.id", ondelete="SET NULL"), index=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GenerationTask(IdMixin, TimestampMixin, Base):
    __tablename__ = "generation_tasks"
    __table_args__ = (
        UniqueConstraint(
            "active_dedupe_key",
            name="uq_generation_tasks_active_dedupe_key",
        ),
        UniqueConstraint(
            "project_id",
            "task_type",
            "idempotency_key",
            name="uq_generation_tasks_idempotency_scope",
        ),
    )

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    parent_task_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("generation_tasks.id", ondelete="SET NULL"),
        index=True,
    )
    retry_of_task_id: Mapped[str | None] = mapped_column(String(36), index=True)
    resource_key: Mapped[str | None] = mapped_column(String(255), index=True)
    active_dedupe_key: Mapped[str | None] = mapped_column(String(255), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), index=True)
    provider: Mapped[str | None] = mapped_column(String(120), index=True)
    model: Mapped[str | None] = mapped_column(String(160))
    provider_task_id: Mapped[str | None] = mapped_column(String(255), index=True)
    input_payload: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    output_asset_ids: Mapped[list] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    result_payload: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False, index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_label: Mapped[str | None] = mapped_column(String(160))
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    cost_estimate: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    raw_response: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(160), index=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    parent: Mapped["GenerationTask | None"] = relationship(
        "GenerationTask",
        remote_side="GenerationTask.id",
        back_populates="children",
    )
    children: Mapped[list["GenerationTask"]] = relationship(
        "GenerationTask",
        back_populates="parent",
        lazy="selectin",
    )

    asset_candidates: Mapped[list[AssetCandidate]] = relationship(
        "AssetCandidate",
        primaryjoin=lambda: GenerationTask.id == foreign(AssetCandidate.source_task_id),
        viewonly=True,
        lazy="selectin",
    )

    @validates("input_payload", "result_payload", "raw_response")
    def compact_audit_payload(self, _key: str, value: dict | list) -> dict | list:
        return compact_json_payload(value)

    @property
    def child_summary(self) -> dict[str, int]:
        superseded_ids = {
            child.retry_of_task_id
            for child in self.children
            if child.retry_of_task_id
        }
        effective_children = [
            child for child in self.children if child.id not in superseded_ids
        ]
        summary = {
            "total": len(effective_children),
            "attempt_count": len(self.children),
            "queued": 0,
            "running": 0,
            "waiting_provider": 0,
            "waiting_children": 0,
            "cancelling": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
        }
        for child in effective_children:
            if child.status in summary:
                summary[child.status] += 1
        return summary

    @property
    def context_links(self) -> list[dict[str, str | None]]:
        links: list[dict[str, str | None]] = []
        seen: set[tuple[str, str, str]] = set()

        def add(target_type: str, target_id: object, role: str, label: str | None = None) -> None:
            if target_id is None:
                return
            value = str(target_id).strip()
            if not value:
                return
            key = (target_type, value, role)
            if key in seen:
                return
            seen.add(key)
            links.append({"target_type": target_type, "target_id": value, "role": role, "label": label})

        def add_many(target_type: str, values: object, role: str, label: str | None = None) -> None:
            if values is None:
                return
            if isinstance(values, list):
                for value in values:
                    add(target_type, value, role, label)
                return
            add(target_type, values, role, label)

        payload = self.input_payload if isinstance(self.input_payload, dict) else {}
        result = self.result_payload if isinstance(self.result_payload, dict) else {}
        asset_resolution = payload.get("asset_resolution") if isinstance(payload.get("asset_resolution"), dict) else {}
        reference_metadata = (
            asset_resolution.get("reference_metadata")
            if isinstance(asset_resolution.get("reference_metadata"), dict)
            else {}
        )
        add("project", self.project_id, "owner", "项目")
        add("chapter", payload.get("chapter_id"), "input", "章节")
        add("script", payload.get("script_id") or payload.get("source_script_id"), "input", "剧本")
        add("shot", payload.get("shot_id"), "input", "镜头")
        add("shot", asset_resolution.get("shot_id"), "input", "镜头")
        add_many("shot", payload.get("shot_ids"), "input", "镜头")
        add("asset", payload.get("asset_id"), "input", "资产")
        add("asset", payload.get("source_asset_id"), "input", "源资产")
        add("asset", payload.get("image_asset_id"), "input", "输入图片")
        add_many("asset", payload.get("image_asset_ids"), "input", "输入图片")
        add_many("asset", payload.get("reference_asset_ids"), "input", "参考资产")
        add_many("asset", reference_metadata.get("reference_asset_ids"), "input", "参考资产")
        add_many("character", payload.get("character_ids"), "input", "角色")
        add_many("scene", payload.get("scene_ids"), "input", "场景")
        add_many("prop", payload.get("prop_ids"), "input", "道具")
        add_many("asset", self.output_asset_ids, "output", "输出资产")
        add_many("asset_candidate", result.get("candidate_ids"), "output", "候选资产")
        add_many("asset_candidate", [candidate.id for candidate in self.asset_candidates], "output", "候选资产")
        return links


class Export(IdMixin, TimestampMixin, Base):
    __tablename__ = "exports"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("assets.id", ondelete="SET NULL"),
        index=True,
    )
    resolution: Mapped[str] = mapped_column(String(32), default="1280x720", nullable=False)
    duration_sec: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    format: Mapped[str] = mapped_column(String(40), default="mp4", nullable=False)
    subtitle_mode: Mapped[str] = mapped_column(String(20), default="none", nullable=False)
    ffmpeg_command: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False, index=True)

    asset: Mapped[Asset | None] = relationship(back_populates="export")
