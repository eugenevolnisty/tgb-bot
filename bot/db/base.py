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
        for v in ["property", "cargo", "accident", "expeditor", "cmr", "dms", "other"]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text(f"ALTER TYPE quote_type ADD VALUE IF NOT EXISTS '{v}'"))
            except Exception:
                pass

        # Extend payment_status for existing DBs (if table/payment_status already existed).
        for v in ["pending", "paid"]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text(f"ALTER TYPE payment_status ADD VALUE IF NOT EXISTS '{v}'"))
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

        # Contract: vehicle description for KASKO.
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "ALTER TABLE IF EXISTS contracts "
                        "ADD COLUMN IF NOT EXISTS vehicle_description TEXT NULL"
                    )
                )
        except Exception:
            pass

        # Application notes and reminder->note link.
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS application_notes ("
                        "id SERIAL PRIMARY KEY, "
                        "application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE, "
                        "agent_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
                        "text TEXT NOT NULL, "
                        "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                        ")"
                    )
                )
        except Exception:
            pass
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS ix_application_notes_application_id ON application_notes (application_id)",
            "CREATE INDEX IF NOT EXISTS ix_application_notes_agent_user_id ON application_notes (agent_user_id)",
        ]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text(idx_sql))
            except Exception:
                pass
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "ALTER TABLE IF EXISTS reminders "
                        "ADD COLUMN IF NOT EXISTS note_id INTEGER NULL REFERENCES application_notes(id) ON DELETE SET NULL"
                    )
                )
        except Exception:
            pass
        try:
            async with conn.begin_nested():
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_reminders_note_id ON reminders (note_id)"))
        except Exception:
            pass

        # Agent commission settings (company + insurance kind + percent).
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS agent_commissions ("
                        "id SERIAL PRIMARY KEY, "
                        "agent_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
                        "company VARCHAR(200) NOT NULL, "
                        "contract_kind VARCHAR(200) NOT NULL, "
                        "percent_bp INTEGER NOT NULL, "
                        "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                        ")"
                    )
                )
        except Exception:
            pass
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS ix_agent_commissions_agent_user_id ON agent_commissions (agent_user_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_commissions_agent_company_kind "
            "ON agent_commissions (agent_user_id, company, contract_kind)",
        ]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text(idx_sql))
            except Exception:
                pass

        # Multi-tenant base tables and user binding.
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS tenants ("
                        "id SERIAL PRIMARY KEY, "
                        "code VARCHAR(64) NOT NULL UNIQUE, "
                        "title VARCHAR(200) NOT NULL, "
                        "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                        ")"
                    )
                )
        except Exception:
            pass
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "INSERT INTO tenants (code, title) "
                        "SELECT 'default', 'Default tenant' "
                        "WHERE NOT EXISTS (SELECT 1 FROM tenants WHERE code='default')"
                    )
                )
        except Exception:
            pass
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "ALTER TABLE IF EXISTS users "
                        "ADD COLUMN IF NOT EXISTS tenant_id INTEGER NULL REFERENCES tenants(id) ON DELETE SET NULL"
                    )
                )
        except Exception:
            pass
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "ALTER TABLE IF EXISTS users "
                        "ADD COLUMN IF NOT EXISTS display_name VARCHAR(200) NULL"
                    )
                )
        except Exception:
            pass
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "ALTER TABLE IF EXISTS users "
                        "ADD COLUMN IF NOT EXISTS agent_contact_phones_json TEXT NULL"
                    )
                )
        except Exception:
            pass
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "ALTER TABLE IF EXISTS users "
                        "ADD COLUMN IF NOT EXISTS agent_contact_email VARCHAR(200) NULL"
                    )
                )
        except Exception:
            pass
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "ALTER TABLE IF EXISTS users "
                        "ADD COLUMN IF NOT EXISTS agent_contact_telegram VARCHAR(100) NULL"
                    )
                )
        except Exception:
            pass
        try:
            async with conn.begin_nested():
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_tenant_id ON users (tenant_id)"))
        except Exception:
            pass
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "UPDATE users "
                        "SET tenant_id = (SELECT id FROM tenants WHERE code='default' LIMIT 1) "
                        "WHERE tenant_id IS NULL"
                    )
                )
        except Exception:
            pass

        # Agent auth credentials.
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS agent_credentials ("
                        "id SERIAL PRIMARY KEY, "
                        "user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE, "
                        "password_hash VARCHAR(255) NOT NULL, "
                        "salt VARCHAR(255) NOT NULL, "
                        "failed_attempts INTEGER NOT NULL DEFAULT 0, "
                        "locked_until TIMESTAMPTZ NULL, "
                        "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                        ")"
                    )
                )
        except Exception:
            pass

        # Agent invite links (tenant onboarding skeleton).
        try:
            async with conn.begin_nested():
                await conn.execute(text("CREATE TYPE invite_status AS ENUM ('active','used','revoked','expired')"))
        except Exception:
            pass
        for v in ["active", "used", "revoked", "expired"]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text(f"ALTER TYPE invite_status ADD VALUE IF NOT EXISTS '{v}'"))
            except Exception:
                pass
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS agent_invites ("
                        "id SERIAL PRIMARY KEY, "
                        "tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, "
                        "agent_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
                        "token VARCHAR(96) NOT NULL UNIQUE, "
                        "status invite_status NOT NULL DEFAULT 'active', "
                        "uses_left INTEGER NOT NULL DEFAULT 1, "
                        "expires_at TIMESTAMPTZ NULL, "
                        "used_at TIMESTAMPTZ NULL, "
                        "used_by_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL, "
                        "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                        ")"
                    )
                )
        except Exception:
            pass
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS ix_agent_invites_tenant_id ON agent_invites (tenant_id)",
            "CREATE INDEX IF NOT EXISTS ix_agent_invites_agent_user_id ON agent_invites (agent_user_id)",
            "CREATE INDEX IF NOT EXISTS ix_agent_invites_used_by_user_id ON agent_invites (used_by_user_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_invites_token ON agent_invites (token)",
        ]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text(idx_sql))
            except Exception:
                pass
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "ALTER TABLE IF EXISTS agent_invites "
                        "ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT FALSE"
                    )
                )
        except Exception:
            pass
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "ALTER TABLE IF EXISTS agent_invites "
                        "ADD COLUMN IF NOT EXISTS target_client_id INTEGER NULL REFERENCES clients(id) ON DELETE SET NULL"
                    )
                )
        except Exception:
            pass
        try:
            async with conn.begin_nested():
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_invites_is_public ON agent_invites (is_public)"))
        except Exception:
            pass
        try:
            async with conn.begin_nested():
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_invites_target_client_id ON agent_invites (target_client_id)"))
        except Exception:
            pass
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "ALTER TABLE IF EXISTS clients "
                        "ADD COLUMN IF NOT EXISTS source_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL"
                    )
                )
        except Exception:
            pass
        try:
            async with conn.begin_nested():
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_clients_source_user_id ON clients (source_user_id)"))
        except Exception:
            pass
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ux_clients_source_user_id_not_null "
                        "ON clients (source_user_id) WHERE source_user_id IS NOT NULL"
                    )
                )
        except Exception:
            pass

        # Contract: insured sum (coverage amount).
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "ALTER TABLE IF EXISTS contracts "
                        "ADD COLUMN IF NOT EXISTS insured_sum_minor INTEGER NULL"
                    )
                )
        except Exception:
            pass

        # Contract status: active / terminated.
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text("CREATE TYPE contract_status AS ENUM ('active','terminated')")
                )
        except Exception:
            pass
        for v in ["active", "terminated"]:
            try:
                async with conn.begin_nested():
                    await conn.execute(
                        text(f"ALTER TYPE contract_status ADD VALUE IF NOT EXISTS '{v}'")
                    )
            except Exception:
                pass
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "ALTER TABLE IF EXISTS contracts "
                        "ADD COLUMN IF NOT EXISTS status contract_status "
                        "NOT NULL DEFAULT 'active'"
                    )
                )
        except Exception:
            pass
