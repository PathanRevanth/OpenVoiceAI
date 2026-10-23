"""Async database engine/session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from voicefirst.db.models import Base


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await self._add_missing_columns(conn)

    @staticmethod
    async def _add_missing_columns(conn: Any) -> None:
        """Lightweight schema evolution: add columns that exist on models but
        not yet in the database (create_all never alters existing tables)."""
        from voicefirst.db.models import Base

        for table in Base.metadata.sorted_tables:
            try:
                result = await conn.exec_driver_sql(f"PRAGMA table_info({table.name})")
                rows = result.fetchall() if result else []
            except Exception:  # noqa: BLE001 - postgres path below
                rows = []
            if rows:
                existing = {row[1] for row in rows}
            else:
                try:
                    result = await conn.exec_driver_sql(
                        "SELECT column_name FROM information_schema.columns "
                        f"WHERE table_name = '{table.name}'"
                    )
                    existing = {r[0] for r in result.fetchall()}
                except Exception:  # noqa: BLE001
                    continue

            for column in table.columns:
                if column.name in existing:
                    continue
                col_type = column.type.compile(dialect=conn.dialect)
                default = ""
                if column.nullable is False:
                    default = " NOT NULL DEFAULT 0" if column.type.python_type is int else " NOT NULL DEFAULT ''"
                try:
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
