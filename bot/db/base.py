from functools import lru_cache
import json

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
        # Extend user_role enum for PostgreSQL before metadata sync.
        # On non-PostgreSQL backends this may fail; keep init flow resilient.
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'superadmin'")
                )
        except Exception:
            pass

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
                        "ALTER TABLE IF EXISTS agent_invites "
                        "ADD COLUMN IF NOT EXISTS invite_type VARCHAR(20) NOT NULL DEFAULT 'client'"
                    )
                )
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


async def migrate_agent_tenants() -> None:
    """
    Одноразовая миграция: создаёт персональный
    тенант для каждого агента у которого
    tenant_id указывает на "default" тенант.
    Безопасно запускать повторно.
    """
    from sqlalchemy import select, update

    from bot.db.models import AgentInvite, Tenant, User, UserRole

    async with get_session_maker()() as session:
        # Найти дефолтный тенант
        res = await session.execute(
            select(Tenant).where(Tenant.code == "default")
        )
        default_tenant = res.scalar_one_or_none()
        if default_tenant is None:
            return

        # Найти всех агентов на дефолтном тенанте
        res = await session.execute(
            select(User).where(
                User.role == UserRole.agent,
                User.tenant_id == default_tenant.id,
            )
        )
        agents = list(res.scalars().all())

        for agent in agents:
            # Создать персональный тенант
            code = f"agent_{agent.tg_id}"
            res = await session.execute(
                select(Tenant).where(Tenant.code == code)
            )
            personal_tenant = res.scalar_one_or_none()
            if personal_tenant is None:
                personal_tenant = Tenant(
                    code=code,
                    title=agent.display_name or f"Агент {agent.tg_id}",
                )
                session.add(personal_tenant)
                await session.flush()

            # Переключить агента на персональный тенант
            agent.tenant_id = personal_tenant.id

            # Обновить все агентские инвайты
            await session.execute(
                update(AgentInvite)
                .where(AgentInvite.agent_user_id == agent.id)
                .values(tenant_id=personal_tenant.id)
            )

        await session.commit()


async def migrate_tariff_tables() -> None:
    """
    Создаёт таблицы тарифной системы
    если не существуют. Используем
    CREATE TABLE IF NOT EXISTS +
    ALTER TABLE ADD COLUMN IF NOT EXISTS
    для безопасного добавления.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        # 1) insurance_companies
        for sql in [
            (
                "CREATE TABLE IF NOT EXISTS insurance_companies ("
                "id SERIAL PRIMARY KEY, "
                "tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, "
                "name VARCHAR(200) NOT NULL, "
                "is_active BOOLEAN NOT NULL DEFAULT TRUE, "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "CONSTRAINT uq_insurance_companies_tenant_name UNIQUE (tenant_id, name)"
                ")"
            ),
            "ALTER TABLE IF EXISTS insurance_companies ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE IF EXISTS insurance_companies ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
            "CREATE INDEX IF NOT EXISTS ix_insurance_companies_tenant_id ON insurance_companies (tenant_id)",
        ]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text(sql))
            except Exception:
                pass

        # 2) insurance_types
        for sql in [
            (
                "CREATE TABLE IF NOT EXISTS insurance_types ("
                "id SERIAL PRIMARY KEY, "
                "tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, "
                "company_id INTEGER NOT NULL REFERENCES insurance_companies(id) ON DELETE CASCADE, "
                "type_key VARCHAR(50) NOT NULL, "
                "custom_name VARCHAR(200) NULL, "
                "is_active BOOLEAN NOT NULL DEFAULT TRUE, "
                "sort_order INTEGER NOT NULL DEFAULT 0, "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                ")"
            ),
            "ALTER TABLE IF EXISTS insurance_types ADD COLUMN IF NOT EXISTS custom_name VARCHAR(200) NULL",
            "ALTER TABLE IF EXISTS insurance_types ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE IF EXISTS insurance_types ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE IF EXISTS insurance_types ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
            "CREATE INDEX IF NOT EXISTS ix_insurance_types_tenant_id ON insurance_types (tenant_id)",
            "CREATE INDEX IF NOT EXISTS ix_insurance_types_company_id ON insurance_types (company_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_insurance_types_non_other "
            "ON insurance_types (tenant_id, company_id, type_key) WHERE type_key <> 'other'",
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_insurance_types_other "
            "ON insurance_types (tenant_id, company_id, type_key, custom_name) WHERE type_key = 'other'",
        ]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text(sql))
            except Exception:
                pass

        # 3) tariff_cards
        for sql in [
            (
                "CREATE TABLE IF NOT EXISTS tariff_cards ("
                "id SERIAL PRIMARY KEY, "
                "tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, "
                "company_id INTEGER NULL REFERENCES insurance_companies(id) ON DELETE SET NULL, "
                "insurance_type_id INTEGER NULL REFERENCES insurance_types(id) ON DELETE SET NULL, "
                "card_type VARCHAR(30) NOT NULL, "
                "config TEXT NOT NULL, "
                "is_active BOOLEAN NOT NULL DEFAULT TRUE, "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "CONSTRAINT uq_tariff_cards_scope UNIQUE (tenant_id, company_id, insurance_type_id)"
                ")"
            ),
            "ALTER TABLE IF EXISTS tariff_cards ADD COLUMN IF NOT EXISTS card_type VARCHAR(30) NOT NULL DEFAULT 'percentage'",
            "ALTER TABLE IF EXISTS tariff_cards ADD COLUMN IF NOT EXISTS config TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE IF EXISTS tariff_cards ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE IF EXISTS tariff_cards ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
            "ALTER TABLE IF EXISTS tariff_cards ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
            "CREATE INDEX IF NOT EXISTS ix_tariff_cards_tenant_id ON tariff_cards (tenant_id)",
            "CREATE INDEX IF NOT EXISTS ix_tariff_cards_company_id ON tariff_cards (company_id)",
            "CREATE INDEX IF NOT EXISTS ix_tariff_cards_insurance_type_id ON tariff_cards (insurance_type_id)",
        ]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text(sql))
            except Exception:
                pass

        # 4) insurance_type_documents
        for sql in [
            (
                "CREATE TABLE IF NOT EXISTS insurance_type_documents ("
                "id SERIAL PRIMARY KEY, "
                "insurance_type_id INTEGER NOT NULL REFERENCES insurance_types(id) ON DELETE CASCADE, "
                "tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, "
                "file_id VARCHAR(250) NOT NULL, "
                "file_unique_id VARCHAR(250) NULL, "
                "caption TEXT NULL, "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                ")"
            ),
            "ALTER TABLE IF EXISTS insurance_type_documents ADD COLUMN IF NOT EXISTS file_unique_id VARCHAR(250) NULL",
            "ALTER TABLE IF EXISTS insurance_type_documents ADD COLUMN IF NOT EXISTS caption TEXT NULL",
            "ALTER TABLE IF EXISTS insurance_type_documents ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
            "CREATE INDEX IF NOT EXISTS ix_insurance_type_documents_insurance_type_id ON insurance_type_documents (insurance_type_id)",
            "CREATE INDEX IF NOT EXISTS ix_insurance_type_documents_tenant_id ON insurance_type_documents (tenant_id)",
        ]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text(sql))
            except Exception:
                pass

        # 5) default_tariffs
        for sql in [
            (
                "CREATE TABLE IF NOT EXISTS default_tariffs ("
                "id SERIAL PRIMARY KEY, "
                "type_key VARCHAR(50) NOT NULL UNIQUE, "
                "card_type VARCHAR(30) NOT NULL, "
                "config TEXT NOT NULL, "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                ")"
            ),
            "ALTER TABLE IF EXISTS default_tariffs ADD COLUMN IF NOT EXISTS card_type VARCHAR(30) NOT NULL DEFAULT 'percentage'",
            "ALTER TABLE IF EXISTS default_tariffs ADD COLUMN IF NOT EXISTS config TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE IF EXISTS default_tariffs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        ]:
            try:
                async with conn.begin_nested():
                    await conn.execute(text(sql))
            except Exception:
                pass

        default_tariffs_data = [
            (
                "kasko",
                "parametric",
                {
                    "base_rate": 3.5,
                    "min_premium": 300,
                    "age_coefficients": {"0-1": 1.0, "2-3": 0.95, "4-5": 0.85, "6-7": 0.75, "8+": 0.65},
                    "deductible_discount": {"0": 1.0, "100": 0.95, "300": 0.85, "500": 0.75},
                },
            ),
            (
                "osago",
                "table",
                {"rates": {"до 70 л.с.": 180, "70-100 л.с.": 270, "100-150 л.с.": 420, "150+ л.с.": 600}},
            ),
            ("property", "percentage", {"rate": 0.5, "min_premium": 50}),
            (
                "expeditor",
                "packages",
                {
                    "packages": {
                        "Базовый": {"price": 500, "limit": "50 000 EUR"},
                        "Оптимальный": {"price": 900, "limit": "100 000 EUR"},
                        "Расширенный": {"price": 1500, "limit": "200 000 EUR"},
                        "Максимальный": {"price": 2500, "limit": "500 000 EUR"},
                    }
                },
            ),
            (
                "travel",
                "matrix",
                {
                    "zones": {
                        "Европа": {
                            "variant_A": {"1-7": 15, "8-15": 12, "16-30": 10, "31-90": 8},
                            "variant_B": {"1-7": 25, "8-15": 20, "16-30": 17, "31-90": 14},
                        },
                        "Весь мир": {
                            "variant_A": {"1-7": 25, "8-15": 20, "16-30": 17, "31-90": 14},
                            "variant_B": {"1-7": 40, "8-15": 35, "16-30": 30, "31-90": 25},
                        },
                    }
                },
            ),
            (
                "cmr",
                "parametric",
                {
                    "base_rates": {
                        "general_cargo": 0.15,
                        "dangerous_cargo": 0.35,
                        "perishable": 0.25,
                        "fragile": 0.30,
                    },
                    "limit_coefficients": {"50000": 1.0, "100000": 1.3, "200000": 1.5},
                    "vehicle_count_discount": {"1": 1.0, "2-3": 0.95, "4-5": 0.90, "6+": 0.85},
                },
            ),
        ]

        insert_sql = text(
            "INSERT INTO default_tariffs (type_key, card_type, config) "
            "VALUES (:type_key, :card_type, :config) "
            "ON CONFLICT (type_key) DO NOTHING"
        )
        for type_key, card_type, config in default_tariffs_data:
            try:
                async with conn.begin_nested():
                    await conn.execute(
                        insert_sql,
                        {
                            "type_key": type_key,
                            "card_type": card_type,
                            "config": json.dumps(config, ensure_ascii=False),
                        },
                    )
            except Exception:
                pass
