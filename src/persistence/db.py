"""Async SQLAlchemy engine + session factory.

Singleton pattern keyed off the URL so tests can swap to in-memory SQLite by
calling `init_db("sqlite+aiosqlite:///:memory:")`.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..settings import get_settings


class Base(DeclarativeBase):
    """Common ORM base."""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = get_settings().database_url
        _engine = create_async_engine(url, echo=False, future=True, pool_pre_ping=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def init_db(url: str | None = None) -> None:
    """Create all tables. Pass `url` to override the configured DB.

    Imports models lazily so this module stays import-safe.
    """
    global _engine, _session_factory
    if url is not None:
        if _engine is not None:
            await _engine.dispose()
        _engine = create_async_engine(url, echo=False, future=True)
        _session_factory = async_sessionmaker(
            bind=_engine, expire_on_commit=False, autoflush=False
        )

    from . import models  # noqa: F401  (registers tables on Base.metadata)

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
