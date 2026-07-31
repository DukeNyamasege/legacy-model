from __future__ import annotations

import logging
import os
import time
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.exc import OperationalError

from app.database import normalize_database_url
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
config.set_main_option(
    "sqlalchemy.url",
    normalize_database_url(os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))),
)
target_metadata = Base.metadata
logger = logging.getLogger("alembic.runtime.migration")


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    maximum_attempts = max(
        1,
        int(os.getenv("DATABASE_MIGRATION_CONNECT_ATTEMPTS", "30")),
    )
    retry_seconds = max(
        0.1,
        float(os.getenv("DATABASE_MIGRATION_RETRY_SECONDS", "2")),
    )

    try:
        for attempt in range(1, maximum_attempts + 1):
            try:
                with connectable.connect() as connection:
                    context.configure(
                        connection=connection,
                        target_metadata=target_metadata,
                    )
                    with context.begin_transaction():
                        context.run_migrations()
                return
            except OperationalError:
                if attempt >= maximum_attempts:
                    raise
                logger.warning(
                    "Database is not ready for migrations; retrying in %.1f seconds "
                    "(attempt %s/%s)",
                    retry_seconds,
                    attempt,
                    maximum_attempts,
                )
                time.sleep(retry_seconds)
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
