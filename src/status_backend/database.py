"""Async SQLite database."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .core.config import get_settings
from .models import Base

DATA_DIR = Path(get_settings().data_dir)
DATABASE_PATH = DATA_DIR / "status.db"

engine = create_async_engine(
    f"sqlite+aiosqlite:///{DATABASE_PATH}",
    connect_args={"check_same_thread": False},
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Composite index for history aggregations (covers existing databases too)
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_checks_monitor_checked "
                "ON checks (monitor_id, checked_at)"
            )
        )


async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
