from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings


class DatabaseNotConfigured(RuntimeError):
    pass


@lru_cache
def get_engine():
    """Create lazily; imports and non-database APIs require no credentials/server."""
    url = get_settings().database_url
    if url is None:
        raise DatabaseNotConfigured("DATABASE_URL is not configured")
    return create_engine(
        url.get_secret_value(), pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"}, hide_parameters=True,
    )


@lru_cache
def get_session_factory():
    return sessionmaker(bind=get_engine(), class_=Session, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
