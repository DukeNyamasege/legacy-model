from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


class Database:
    def __init__(self, url: str) -> None:
        self.url = normalize_database_url(url)
        if self.url.startswith("sqlite:///"):
            path = Path(self.url.removeprefix("sqlite:///"))
            path.parent.mkdir(parents=True, exist_ok=True)
        is_sqlite = self.url.startswith("sqlite")
        connect_args = {"check_same_thread": False} if is_sqlite else {}
        engine_options: dict[str, object] = {
            "pool_pre_ping": True,
            "connect_args": connect_args,
        }
        if not is_sqlite:
            engine_options.update(
                {
                    "pool_size": max(5, int(os.getenv("DATABASE_POOL_SIZE", "15"))),
                    "max_overflow": max(
                        0, int(os.getenv("DATABASE_MAX_OVERFLOW", "15"))
                    ),
                    "pool_timeout": max(
                        1, int(os.getenv("DATABASE_POOL_TIMEOUT_SECONDS", "10"))
                    ),
                    "pool_recycle": max(
                        30, int(os.getenv("DATABASE_POOL_RECYCLE_SECONDS", "300"))
                    ),
                }
            )
        self.engine: Engine = create_engine(self.url, **engine_options)
        self.session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=Session
        )

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
