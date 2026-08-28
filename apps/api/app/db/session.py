import atexit
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
import shutil

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base


def create_database_engine(
    database_url: str,
    *,
    database_schema: str | None = None,
) -> Engine:
    url = make_url(database_url)
    connect_args: dict[str, object] = {}

    if url.get_backend_name() == "sqlite":
        database_path = url.database
        if database_path and database_path != ":memory:":
            Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        connect_args = {
            "check_same_thread": False,
            "timeout": 30,
        }
    elif url.get_backend_name() == "postgresql" and database_schema:
        connect_args = {"options": f"-csearch_path={database_schema}"}

    database_engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if url.get_backend_name() == "sqlite":
        event.listen(database_engine, "connect", _configure_sqlite_connection)

    return database_engine


def _configure_sqlite_connection(dbapi_connection: object, _connection_record: object) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def initialize_database(database_engine: Engine | None = None) -> None:
    target_engine = database_engine or engine
    if target_engine.dialect.name != "sqlite":
        return

    import app.models  # noqa: F401

    Base.metadata.create_all(bind=target_engine)
    _ensure_local_asset_spec_columns(target_engine)
    _migrate_local_animation_english_workflow(target_engine)
    _migrate_local_task_runtime(target_engine)
    _normalize_local_task_runtime(target_engine)


def _ensure_local_asset_spec_columns(database_engine: Engine) -> None:
    """Add JSON columns that create_all cannot backfill into an existing SQLite DB."""
    inspector = inspect(database_engine)
    required_columns = {
        "characters": "asset_spec",
        "scenes": "asset_spec",
        "props": "asset_spec",
    }
    with database_engine.begin() as connection:
        for table_name, column_name in required_columns.items():
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            if column_name in existing:
                continue
            connection.execute(
                text(
                    f"ALTER TABLE {table_name} "
                    f"ADD COLUMN {column_name} JSON NOT NULL DEFAULT '{{}}'"
                )
            )


def _migrate_local_animation_english_workflow(database_engine: Engine) -> None:
    """Versioned SQLite migration for the animation-English workflow.

    Existing local databases are backed up before the two legacy TTS columns are
    dropped. Historical media files and asset rows are intentionally untouched.
    """

    migration_id = "20260730_animation_english_workflow_v1"
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS local_schema_migrations ("
                "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
        )
        already_applied = connection.execute(
            text("SELECT 1 FROM local_schema_migrations WHERE version = :version"),
            {"version": migration_id},
        ).scalar_one_or_none()
    if already_applied:
        return

    inspector = inspect(database_engine)
    existing_tables = set(inspector.get_table_names())
    character_columns = _sqlite_column_names(inspector, "characters")
    dialogue_columns = _sqlite_column_names(inspector, "dialogues")
    destructive = "voice_profile" in character_columns or "audio_asset_id" in dialogue_columns
    if destructive:
        _backup_local_database(database_engine, migration_id)

    additions: dict[str, dict[str, str]] = {
        "projects": {
            "creative_settings": (
                "JSON NOT NULL DEFAULT "
                """'{"english_level":"A1","animation_style":"高品质风格化三维儿童动画","dialogue_language":"en","translation_language":"zh-CN","default_subtitle_mode":"none"}'"""
            ),
        },
        "chapters": {
            "input_mode": "VARCHAR(40) NOT NULL DEFAULT 'imported_script'",
        },
        "dialogues": {
            "speaker_name": "VARCHAR(120) NOT NULL DEFAULT 'Character'",
            "translation_zh": "TEXT",
            "sequence_order": "INTEGER NOT NULL DEFAULT 0",
            "beat_id": "VARCHAR(80)",
            "sound_cues": "JSON NOT NULL DEFAULT '[]'",
        },
        "assets": {
            "variant_key": "VARCHAR(120)",
        },
        "asset_candidates": {
            "variant_key": "VARCHAR(120)",
        },
        "exports": {
            "subtitle_mode": "VARCHAR(20) NOT NULL DEFAULT 'none'",
        },
    }

    with database_engine.begin() as connection:
        connection_inspector = inspect(connection)
        for table_name, columns in additions.items():
            if table_name not in existing_tables:
                continue
            current = _sqlite_column_names(connection_inspector, table_name)
            for column_name, definition in columns.items():
                if column_name not in current:
                    connection.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
                    )
                    current.add(column_name)

        if "assets" in existing_tables:
            connection.execute(
                text(
                    "UPDATE assets SET variant_key = 'base' "
                    "WHERE variant_key IS NULL AND entity_type IN ('character', 'scene', 'prop')"
                )
            )
        if "asset_candidates" in existing_tables:
            connection.execute(
                text(
                    "UPDATE asset_candidates SET variant_key = 'base' "
                    "WHERE variant_key IS NULL AND entity_type IN ('character', 'scene', 'prop')"
                )
            )
        if "provider_configs" in existing_tables:
            connection.execute(text("DELETE FROM provider_configs WHERE provider_type = 'tts'"))
        if "agent_configs" in existing_tables:
            connection.execute(
                text("UPDATE agent_configs SET is_active = 0 WHERE agent_type = 'sound_designer'")
            )
        if "prompt_versions" in existing_tables:
            connection.execute(
                text("UPDATE prompt_versions SET is_active = 0 WHERE agent_type = 'sound_designer'")
            )

        connection.execute(text("DROP INDEX IF EXISTS ix_dialogues_audio_asset_id"))
        if "audio_asset_id" in dialogue_columns:
            connection.execute(text("ALTER TABLE dialogues DROP COLUMN audio_asset_id"))
        if "voice_profile" in character_columns:
            connection.execute(text("ALTER TABLE characters DROP COLUMN voice_profile"))
        if "project_stages" in existing_tables:
            connection.execute(text("DROP TABLE project_stages"))

        connection.execute(
            text(
                "INSERT INTO local_schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": migration_id,
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )


def _migrate_local_task_runtime(database_engine: Engine) -> None:
    migration_id = "20260824_unified_task_runtime_v1"
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS local_schema_migrations ("
                "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
        )
        if connection.execute(
            text("SELECT 1 FROM local_schema_migrations WHERE version = :version"),
            {"version": migration_id},
        ).scalar_one_or_none():
            return

    inspector = inspect(database_engine)
    if "generation_tasks" not in set(inspector.get_table_names()):
        return
    existing = _sqlite_column_names(inspector, "generation_tasks")
    additions = {
        "parent_task_id": "VARCHAR(36) REFERENCES generation_tasks(id) ON DELETE SET NULL",
        "retry_of_task_id": "VARCHAR(36)",
        "resource_key": "VARCHAR(255)",
        "active_dedupe_key": "VARCHAR(255)",
        "idempotency_key": "VARCHAR(255)",
        "max_retries": "INTEGER NOT NULL DEFAULT 1",
        "available_at": "DATETIME",
        "lease_owner": "VARCHAR(160)",
        "lease_expires_at": "DATETIME",
        "heartbeat_at": "DATETIME",
        "cancel_requested_at": "DATETIME",
    }
    missing = [column for column in additions if column not in existing]
    if missing:
        _backup_local_database(database_engine, migration_id)

    with database_engine.begin() as connection:
        for column in missing:
            connection.execute(
                text(f"ALTER TABLE generation_tasks ADD COLUMN {column} {additions[column]}")
            )
        current_columns = existing | set(missing)
        legacy_failure_assignments = _local_task_failure_assignments(
            current_columns,
            code="provider_submission_uncertain",
            message="Legacy video task was interrupted during provider submission",
            label="Migration stopped an uncertain legacy submission",
        )
        connection.execute(
            text(
                f"UPDATE generation_tasks SET {legacy_failure_assignments} "
                "WHERE task_type IN ('single_shot_video_candidate_generation', "
                "'shot_video_regeneration_candidate') AND status = 'running'"
            )
        )
        connection.execute(
            text(
                "UPDATE generation_tasks SET task_type = 'shot_video_candidate_generation' "
                "WHERE task_type IN ('single_shot_video_candidate_generation', "
                "'shot_video_regeneration_candidate')"
            )
        )
        connection.execute(
            text(
                "UPDATE generation_tasks SET resource_key = 'project' || char(58) || "
                "project_id || char(58) || 'script' "
                "WHERE task_type = 'script_generation' AND resource_key IS NULL"
            )
        )
        connection.execute(
            text(
                "UPDATE generation_tasks SET resource_key = 'shot' || char(58) || "
                "json_extract(input_payload, '$.shot_id') || char(58) || 'video' "
                "WHERE task_type = 'shot_video_candidate_generation' "
                "AND resource_key IS NULL AND json_extract(input_payload, '$.shot_id') IS NOT NULL"
            )
        )
        connection.execute(
            text(
                f"UPDATE generation_tasks SET "
                f"{_local_task_failure_assignments(current_columns, code='task_dedupe_migration_conflict', message='A duplicate active task was superseded during migration', label='Migration resolved duplicate active task')} "
                "WHERE id IN ("
                "SELECT duplicate.id FROM generation_tasks AS duplicate "
                "JOIN generation_tasks AS keeper "
                "ON keeper.task_type = duplicate.task_type "
                "AND keeper.resource_key = duplicate.resource_key "
                "AND (keeper.created_at < duplicate.created_at "
                "OR (keeper.created_at = duplicate.created_at AND keeper.id < duplicate.id)) "
                "WHERE duplicate.resource_key IS NOT NULL "
                "AND duplicate.status IN ('queued', 'running', 'waiting_provider', "
                "'waiting_children', 'cancelling') "
                "AND keeper.status IN ('queued', 'running', 'waiting_provider', "
                "'waiting_children', 'cancelling'))"
            )
        )
        connection.execute(
            text(
                "UPDATE generation_tasks SET active_dedupe_key = task_type || char(58) || resource_key "
                "WHERE resource_key IS NOT NULL AND active_dedupe_key IS NULL "
                "AND status IN ('queued', 'running', 'waiting_provider', "
                "'waiting_children', 'cancelling')"
            )
        )
        connection.execute(
            text(
                "UPDATE generation_tasks SET available_at = COALESCE(available_at, created_at) "
                "WHERE status IN ('queued', 'waiting_provider')"
            )
        )
        connection.execute(
            text(
                "UPDATE generation_tasks SET lease_expires_at = :expired_at "
                "WHERE status IN ('running', 'cancelling') AND lease_expires_at IS NULL "
                "AND task_type IN ('script_generation', 'shot_video_candidate_generation')"
            ),
            {"expired_at": "1970-01-01T00:00:00+00:00"},
        )
        for column in (
            "parent_task_id",
            "retry_of_task_id",
            "resource_key",
            "active_dedupe_key",
            "idempotency_key",
            "available_at",
            "lease_owner",
            "lease_expires_at",
        ):
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_generation_tasks_{column} "
                    f"ON generation_tasks ({column})"
                )
            )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_generation_tasks_active_dedupe_key "
                "ON generation_tasks (active_dedupe_key) WHERE active_dedupe_key IS NOT NULL"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_generation_tasks_idempotency_scope "
                "ON generation_tasks (project_id, task_type, idempotency_key) "
                "WHERE idempotency_key IS NOT NULL"
            )
        )
        connection.execute(
            text(
                "INSERT INTO local_schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": migration_id,
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )


def _normalize_local_task_runtime(database_engine: Engine) -> None:
    """One-time safety normalization for databases that applied v1 pre-release."""
    migration_id = "20260824_unified_task_runtime_v2"
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS local_schema_migrations ("
                "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
        )
        if connection.execute(
            text("SELECT 1 FROM local_schema_migrations WHERE version = :version"),
            {"version": migration_id},
        ).scalar_one_or_none():
            return

    inspector = inspect(database_engine)
    if "generation_tasks" not in set(inspector.get_table_names()):
        return
    columns = _sqlite_column_names(inspector, "generation_tasks")
    required = {"task_type", "status", "input_payload"}
    if not required.issubset(columns):
        return
    with database_engine.connect() as connection:
        uncertain_count = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM generation_tasks "
                    "WHERE task_type = 'shot_video_candidate_generation' "
                    "AND status = 'running' "
                    "AND json_type(input_payload, '$.provider_request') IS NULL"
                )
            ).scalar_one()
        )
    if uncertain_count:
        _backup_local_database(database_engine, migration_id)

    with database_engine.begin() as connection:
        if uncertain_count:
            connection.execute(
                text(
                    f"UPDATE generation_tasks SET "
                    f"{_local_task_failure_assignments(columns, code='provider_submission_uncertain', message='Legacy video task was interrupted during provider submission', label='Migration stopped an uncertain legacy submission')} "
                    "WHERE task_type = 'shot_video_candidate_generation' "
                    "AND status = 'running' "
                    "AND json_type(input_payload, '$.provider_request') IS NULL"
                )
            )
        connection.execute(
            text(
                "INSERT INTO local_schema_migrations(version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {
                "version": migration_id,
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        )


def _local_task_failure_assignments(
    columns: set[str],
    *,
    code: str,
    message: str,
    label: str,
) -> str:
    assignments = ["status = 'failed'"]
    optional = {
        "error_code": f"error_code = '{code}'",
        "error_message": f"error_message = '{message}'",
        "progress_label": f"progress_label = '{label}'",
        "finished_at": "finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP)",
        "active_dedupe_key": "active_dedupe_key = NULL",
        "lease_owner": "lease_owner = NULL",
        "lease_expires_at": "lease_expires_at = NULL",
    }
    assignments.extend(value for column, value in optional.items() if column in columns)
    return ", ".join(assignments)


def _sqlite_column_names(inspector: object, table_name: str) -> set[str]:
    try:
        return {str(column["name"]) for column in inspector.get_columns(table_name)}
    except Exception:
        return set()


def _backup_local_database(database_engine: Engine, migration_id: str) -> Path | None:
    database_path_value = database_engine.url.database
    if not database_path_value or database_path_value == ":memory:":
        return None
    database_path = Path(database_path_value).expanduser().resolve()
    if not database_path.is_file():
        return None
    if database_engine.dialect.name == "sqlite":
        with database_engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = database_path.with_name(
        f"{database_path.name}.pre-{migration_id}-{timestamp}.bak"
    )
    shutil.copy2(database_path, backup_path)
    return backup_path


engine = create_database_engine(
    settings.effective_database_url,
    database_schema=settings.database_schema,
)
atexit.register(engine.dispose)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)

initialize_database(engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
