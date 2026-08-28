from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import JSON_DOCUMENT
from app.models.base import IdMixin, TimestampMixin


class Project(IdMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    workspace_id: Mapped[str | None] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    style: Mapped[str] = mapped_column(
        Text,
        default="高品质风格化三维儿童动画",
        nullable=False,
    )
    target_duration_sec: Mapped[int | None] = mapped_column(Integer)
    resolution: Mapped[str] = mapped_column(String(32), default="854x480", nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="16:9", nullable=False)
    creative_settings: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    director_memory: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False, index=True)

    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    scripts: Mapped[list["Script"]] = relationship(back_populates="project")
    characters: Mapped[list["Character"]] = relationship(back_populates="project")
    scenes: Mapped[list["Scene"]] = relationship(back_populates="project")
    props: Mapped[list["Prop"]] = relationship(back_populates="project")
    shots: Mapped[list["Shot"]] = relationship(back_populates="project")
    dialogues: Mapped[list["Dialogue"]] = relationship(back_populates="project")


class Chapter(IdMixin, TimestampMixin, Base):
    __tablename__ = "chapters"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    input_mode: Mapped[str] = mapped_column(String(40), default="imported_script", nullable=False, index=True)
    outline: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Compatibility metadata only. Creative planning and quality checks must not
    # use this length proxy to make story decisions.
    source_word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False, index=True)

    project: Mapped[Project] = relationship(back_populates="chapters")
    scripts: Mapped[list["Script"]] = relationship(back_populates="chapter")


class Script(IdMixin, TimestampMixin, Base):
    __tablename__ = "scripts"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chapter_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    scenes: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    dialogues: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="scripts")
    chapter: Mapped[Chapter] = relationship(back_populates="scripts")
    shots: Mapped[list["Shot"]] = relationship(back_populates="script")
    dialogue_rows: Mapped[list["Dialogue"]] = relationship(back_populates="script")


class Character(IdMixin, TimestampMixin, Base):
    __tablename__ = "characters"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role_type: Mapped[str | None] = mapped_column(String(80))
    age: Mapped[str | None] = mapped_column(String(40))
    gender: Mapped[str | None] = mapped_column(String(40))
    identity: Mapped[str | None] = mapped_column(String(255))
    personality: Mapped[str | None] = mapped_column(Text)
    appearance: Mapped[str | None] = mapped_column(Text)
    fixed_prompt: Mapped[str | None] = mapped_column(Text)
    asset_spec: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False, index=True)

    project: Mapped[Project] = relationship(back_populates="characters")
    dialogues: Mapped[list["Dialogue"]] = relationship(back_populates="character")


class Scene(IdMixin, TimestampMixin, Base):
    __tablename__ = "scenes"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    visual_style: Mapped[str | None] = mapped_column(Text)
    atmosphere: Mapped[str | None] = mapped_column(Text)
    fixed_prompt: Mapped[str | None] = mapped_column(Text)
    asset_spec: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False, index=True)

    project: Mapped[Project] = relationship(back_populates="scenes")
    shots: Mapped[list["Shot"]] = relationship(back_populates="scene")


class Prop(IdMixin, TimestampMixin, Base):
    __tablename__ = "props"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    visual_prompt: Mapped[str | None] = mapped_column(Text)
    story_function: Mapped[str | None] = mapped_column(Text)
    asset_spec: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False, index=True)

    project: Mapped[Project] = relationship(back_populates="props")


class Shot(IdMixin, TimestampMixin, Base):
    __tablename__ = "shots"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    script_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("scripts.id", ondelete="SET NULL"),
        index=True,
    )
    scene_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("scenes.id", ondelete="SET NULL"),
        index=True,
    )
    shot_no: Mapped[int] = mapped_column(Integer, nullable=False)
    shot_batch_id: Mapped[str | None] = mapped_column(String(36), index=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    camera_shot: Mapped[str | None] = mapped_column(String(120))
    camera_movement: Mapped[str | None] = mapped_column(String(160))
    duration_sec: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    dialogue_ids: Mapped[list] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    character_ids: Mapped[list] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    prop_ids: Mapped[list] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    shot_card: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    image_prompt: Mapped[str | None] = mapped_column(Text)
    video_prompt: Mapped[str | None] = mapped_column(Text)
    negative_prompt: Mapped[str | None] = mapped_column(Text)
    generation_mode: Mapped[str] = mapped_column(
        String(40),
        default="image_to_video",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False, index=True)

    project: Mapped[Project] = relationship(back_populates="shots")
    script: Mapped[Script | None] = relationship(back_populates="shots")
    scene: Mapped[Scene | None] = relationship(back_populates="shots")
    dialogues: Mapped[list["Dialogue"]] = relationship(back_populates="shot")
    frame_prompts: Mapped[list["ShotFramePrompt"]] = relationship(
        back_populates="shot",
        cascade="all, delete-orphan",
    )
    frame_images: Mapped[list["ShotFrameImage"]] = relationship(
        back_populates="shot",
        cascade="all, delete-orphan",
    )


class ShotFramePrompt(IdMixin, TimestampMixin, Base):
    __tablename__ = "shot_frame_prompts"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    shot_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("shots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    frame_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    layout: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40), default="llm", nullable=False)
    provider: Mapped[str | None] = mapped_column(String(120), index=True)
    model: Mapped[str | None] = mapped_column(String(160))
    raw_response: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="ready_for_review", nullable=False, index=True)

    shot: Mapped[Shot] = relationship(back_populates="frame_prompts")


class ShotFrameImage(IdMixin, TimestampMixin, Base):
    __tablename__ = "shot_frame_images"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    shot_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("shots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    frame_prompt_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("shot_frame_prompts.id", ondelete="SET NULL"),
        index=True,
    )
    asset_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("assets.id", ondelete="SET NULL"),
        index=True,
    )
    frame_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    image_type: Mapped[str] = mapped_column(String(40), default="generated", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(120), index=True)
    model: Mapped[str | None] = mapped_column(String(160))
    prompt: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="ready_for_review", nullable=False, index=True)

    shot: Mapped[Shot] = relationship(back_populates="frame_images")


class Dialogue(IdMixin, TimestampMixin, Base):
    __tablename__ = "dialogues"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    script_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("scripts.id", ondelete="SET NULL"),
        index=True,
    )
    character_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("characters.id", ondelete="SET NULL"),
        index=True,
    )
    shot_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("shots.id", ondelete="SET NULL"),
        index=True,
    )
    speaker_name: Mapped[str] = mapped_column(String(120), default="Character", nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    translation_zh: Mapped[str | None] = mapped_column(Text)
    emotion: Mapped[str | None] = mapped_column(String(80))
    sequence_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    beat_id: Mapped[str | None] = mapped_column(String(80), index=True)
    sound_cues: Mapped[list] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    start_time: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    end_time: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))

    project: Mapped[Project] = relationship(back_populates="dialogues")
    script: Mapped[Script | None] = relationship(back_populates="dialogue_rows")
    character: Mapped[Character | None] = relationship(back_populates="dialogues")
    shot: Mapped[Shot | None] = relationship(back_populates="dialogues")
