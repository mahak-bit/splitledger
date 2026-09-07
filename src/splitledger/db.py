"""Async SQLAlchemy engine, session factory and the FastAPI session dependency."""

from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# Predictable constraint names keep Alembic autogenerate stable across backends.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    """Create the process-wide engine once and return it."""
    global _engine, _session_factory
    if _engine is None:
        _engine = create_async_engine(url, echo=echo, pool_pre_ping=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False, autoflush=False)
    return _engine


async def dispose_engine() -> None:
    """Close pooled connections and reset module state (shutdown and tests)."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the configured session factory, or explain that setup was skipped."""
    if _session_factory is None:
        msg = "Database engine is not initialised; call init_engine() first."
        raise RuntimeError(msg)
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield one session per request. Services commit; this guarantees rollback."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
