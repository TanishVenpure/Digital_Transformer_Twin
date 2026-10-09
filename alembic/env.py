from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config.settings import get_settings
from app.database.db import DatabaseNotConfigured
from app.models import Base  # Imports all models, registering their metadata.

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    value = get_settings().database_url
    if value is None:
        raise DatabaseNotConfigured("Configure DATABASE_URL before running migrations")
    return value.get_secret_value()


def run_migrations_offline():
    context.configure(
        url=database_url(), target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"}, compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    # Pass URL directly rather than interpolating passwords through alembic.ini.
    engine = create_engine(
        database_url(), poolclass=pool.NullPool,
        connect_args={"connect_timeout": 5}, hide_parameters=True,
    )
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=target_metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
