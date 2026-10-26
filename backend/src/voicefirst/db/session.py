"""Async database engine/session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from voicefirst.db.models import Base


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        if url.startswith("postgresql"):
            # Serverless Postgres (Neon free plan) bills compute time, and an
            # idle pooled connection prevents the compute from scaling to zero.
            # A long-lived Render instance would otherwise exhaust the monthly
            # CU-hour quota in about two days. NullPool closes every connection
            # when the request releases it, so Neon can suspend between turns.
            self.engine: AsyncEngine = create_async_engine(
                url,
                poolclass=NullPool,
                pool_pre_ping=True,
                connect_args={"timeout": 20},
            )
        else:
            self.engine = create_async_engine(url, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await self._add_missing_columns(conn)

    @staticmethod
    async def _add_missing_columns(conn: Any) -> None:
        """Lightweight schema evolution: add columns that exist on models but
        not yet in the database (create_all never alters existing tables).

        Must be dialect-aware: a failed ``PRAGMA`` on PostgreSQL aborts the
        surrounding transaction, which would roll back ``create_all`` too.
        """
        from voicefirst.db.models import Base

        dialect = conn.dialect.name
        for table in Base.metadata.sorted_tables:
            if dialect == "sqlite":
                result = await conn.exec_driver_sql(f"PRAGMA table_info({table.name})")
                existing = {row[1] for row in (result.fetchall() if result else [])}
            elif dialect == "postgresql":
                result = await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = current_schema() "
                        "AND table_name = :table_name"
                    ),
                    {"table_name": table.name},
                )
                existing = {row[0] for row in result.fetchall()}
            else:  # pragma: no cover - unsupported dialect
                continue

            if not existing:
                # Table missing or unreadable; create_all owns creation.
                continue

            for column in table.columns:
                if column.name in existing:
                    continue
                col_type = column.type.compile(dialect=conn.dialect)
                default = ""
                if column.nullable is False:
                    try:
                        python_type = column.type.python_type
                    except (AttributeError, NotImplementedError):
                        python_type = str
                    default = " NOT NULL DEFAULT 0" if python_type is int else " NOT NULL DEFAULT ''"
                try:
                    # Savepoint keeps one failed ALTER from aborting the outer
                    # transaction (PostgreSQL marks it failed until rollback).
                    async with conn.begin_nested():
                        await conn.exec_driver_sql(
                            f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}{default}"
                        )
                except Exception:  # noqa: BLE001 - concurrent or unsupported
                    pass

    async def close(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


def get_db() -> Database:
    """Placeholder used by FastAPI dependency wiring; see `voicefirst.main`."""
    raise RuntimeError("Database dependency not initialized — app lifespan sets `app.state.db`.")


async def get_db_session(db: Database | None = None) -> AsyncIterator[AsyncSession]:
    raise RuntimeError("Use app.state.db.session() via FastAPI dependencies.")
