from logging.config import fileConfig

from alembic import context
from sqlalchemy.schema import CreateSchema

from app.core.config import settings
from app.db.base import Base
from app.db.session import create_database_engine
import app.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(object_: object, name: str, type_: str, reflected: bool, compare_to: object) -> bool:
    del object_, reflected, compare_to
    return not (type_ == "table" and name == "alembic_version")


def get_url() -> str:
    if settings.persistence_mode != "database":
        raise RuntimeError(
            "Alembic is only used for PERSISTENCE_MODE=database. "
            "Local SQLite creates its schema automatically."
        )
    return settings.effective_database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    if settings.database_schema:
        schema_engine = create_database_engine(get_url())
        try:
            with schema_engine.begin() as connection:
                connection.execute(CreateSchema(settings.database_schema, if_not_exists=True))
        finally:
            schema_engine.dispose()

    connectable = create_database_engine(
        get_url(),
        database_schema=settings.database_schema,
    )

    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                version_table_schema=settings.database_schema,
                include_object=include_object,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
