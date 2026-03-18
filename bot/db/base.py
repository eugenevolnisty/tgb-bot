from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from bot.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)


def get_session_maker() -> async_sessionmaker:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def init_db() -> None:
    from bot.db import models  # noqa: F401

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Lightweight "migration" for existing DBs (create_all doesn't ALTER).
        # Safe to run on empty/new DBs as well.
        async with conn.begin_nested():
            await conn.execute(text("ALTER TABLE IF EXISTS applications ADD COLUMN IF NOT EXISTS quote_id INTEGER NULL"))
        async with conn.begin_nested():
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_applications_quote_id ON applications (quote_id)"))

        # Extend enum quote_type for existing DBs (PostgreSQL).
        # If not PostgreSQL, this will likely fail silently at runtime; we ignore errors.
        for v in ["property", "cargo", "accident", "cmr", "dms", "other"]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text(f"ALTER TYPE quote_type ADD VALUE IF NOT EXISTS '{v}'"))
            except Exception:
                pass

        for v in ["sent", "cancelled"]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text(f"ALTER TYPE reminder_status ADD VALUE IF NOT EXISTS '{v}'"))
            except Exception:
                pass

        # Reminder repeat support
        try:
            async with conn.begin_nested():
                await conn.execute(text("CREATE TYPE reminder_repeat AS ENUM ('none','daily','weekly','monthly')"))
        except Exception:
            pass
        for v in ["daily", "weekly", "monthly"]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text(f"ALTER TYPE reminder_repeat ADD VALUE IF NOT EXISTS '{v}'"))
            except Exception:
                pass
        # Add column in one go; if it already exists with correct type, this is a no-op.
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "ALTER TABLE IF EXISTS reminders "
                        "ADD COLUMN IF NOT EXISTS repeat reminder_repeat NOT NULL DEFAULT 'none'"
                    )
                )
        except Exception:
            # If column exists but has different type, skip automatic migration.
            pass
