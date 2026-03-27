import json
import hashlib
import secrets

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from bot.db.base import get_session_maker
from bot.db.models import (
    AgentInvite,
    AgentCommission,
    AgentCredential,
    ApplicationNote,
    Application,
    ApplicationStatus,
    Client,
    ClientDocument,
    ContractDocument,
    Contract,
    ContractStatus,
    Payment,
    PaymentStatus,
    Quote,
    QuoteType,
    Reminder,
    ReminderRepeat,
    ReminderStatus,
    InviteStatus,
    User,
    UserRole,
    Tenant,
)

from sqlalchemy import or_
from datetime import date


async def _get_default_tenant(session) -> Tenant:
    res = await session.execute(select(Tenant).where(Tenant.code == "default"))
    tenant = res.scalar_one_or_none()
    if tenant is not None:
        return tenant
    tenant = Tenant(code="default", title="Default tenant")
    session.add(tenant)
    await session.flush()
    return tenant


async def get_or_create_user(tg_id: int) -> User:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if user is not None:
            if user.tenant_id is None:
                tenant = await _get_default_tenant(session)
                user.tenant_id = tenant.id
                await session.commit()
                await session.refresh(user)
            return user
        tenant = await _get_default_tenant(session)
        user = User(tg_id=tg_id)
        user.tenant_id = tenant.id
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def set_user_role(tg_id: int, role: UserRole) -> User:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        tenant = await _get_default_tenant(session)
        if user is None:
            user = User(tg_id=tg_id, role=role, tenant_id=tenant.id)
            session.add(user)
        else:
            user.role = role
            if user.tenant_id is None:
                user.tenant_id = tenant.id
        await session.commit()
        await session.refresh(user)
        return user


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200000).hex()


def _profile_display_name(first_name: str | None, last_name: str | None, username: str | None, tg_id: int) -> str:
    full = " ".join([p for p in [(first_name or "").strip(), (last_name or "").strip()] if p]).strip()
    if full:
        return full
    if username:
        return f"@{username.strip()}"
    return f"Клиент tg_id={tg_id}"


async def set_agent_password(agent_tg_id: int, password: str) -> bool:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return False
        salt = secrets.token_hex(16)
        ph = _hash_password(password, salt)
        cred_res = await session.execute(select(AgentCredential).where(AgentCredential.user_id == user.id))
        cred = cred_res.scalar_one_or_none()
        if cred is None:
            cred = AgentCredential(user_id=user.id, password_hash=ph, salt=salt, failed_attempts=0, locked_until=None)
            session.add(cred)
        else:
            cred.password_hash = ph
            cred.salt = salt
            cred.failed_attempts = 0
            cred.locked_until = None
        await session.commit()
        return True


async def verify_agent_password(agent_tg_id: int, password: str) -> bool:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return False
        cred_res = await session.execute(select(AgentCredential).where(AgentCredential.user_id == user.id))
        cred = cred_res.scalar_one_or_none()
        if cred is None:
            return False
        if cred.locked_until is not None and cred.locked_until > datetime.now(timezone.utc):
            return False
        ok = secrets.compare_digest(cred.password_hash, _hash_password(password, cred.salt))
        if ok:
            cred.failed_attempts = 0
            cred.locked_until = None
        else:
            cred.failed_attempts += 1
            if cred.failed_attempts >= 5:
                cred.locked_until = (datetime.now(timezone.utc) + timedelta(minutes=15)).replace(microsecond=0)
        await session.commit()
        return ok


async def has_agent_password(agent_tg_id: int) -> bool:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return False
        cred_res = await session.execute(select(AgentCredential.id).where(AgentCredential.user_id == user.id).limit(1))
        return cred_res.scalar_one_or_none() is not None


async def get_user_display_name(tg_id: int) -> str | None:
    async with get_session_maker()() as session:
        res = await session.execute(select(User.display_name).where(User.tg_id == tg_id).limit(1))
        return res.scalar_one_or_none()


async def set_agent_display_name(agent_tg_id: int, display_name: str) -> bool:
    name = (display_name or "").strip()
    if len(name) < 2:
        return False
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return False
        if user.role != UserRole.agent:
            return False
        user.display_name = name[:200]
        await session.commit()
        return True


async def get_agent_contacts(agent_tg_id: int) -> tuple[list[str], str | None, str | None]:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return [], None, None
        phones: list[str] = []
        if user.agent_contact_phones_json:
            try:
                parsed = json.loads(user.agent_contact_phones_json)
                if isinstance(parsed, list):
                    phones = [str(x).strip() for x in parsed if str(x).strip()]
            except Exception:
                phones = []
        return phones, (user.agent_contact_email or None), (user.agent_contact_telegram or None)


async def set_agent_contacts(
    agent_tg_id: int,
    phones: list[str],
    email: str | None,
    telegram: str | None,
) -> bool:
    cleaned_phones = [str(p).strip() for p in phones if str(p).strip()]
    cleaned_email = (email or "").strip() or None
    cleaned_telegram = (telegram or "").strip().lstrip("@") or None
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None or user.role != UserRole.agent:
            return False
        user.agent_contact_phones_json = json.dumps(cleaned_phones, ensure_ascii=False)
        user.agent_contact_email = cleaned_email
        user.agent_contact_telegram = cleaned_telegram
        await session.commit()
        return True


async def has_agent_footprint(tg_id: int) -> bool:
    """
    Dev helper: whether user already has agent-related entities.
    Used to allow role switching for primary tester account.
    """
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return False
        if user.role == UserRole.agent:
            return True
        # Any existing agent credential means this account has agent setup.
        cred_res = await session.execute(select(AgentCredential.id).where(AgentCredential.user_id == user.id).limit(1))
        if cred_res.scalar_one_or_none() is not None:
            return True
        # If account already owns agent-side clients, keep switch enabled for testing.
        client_res = await session.execute(select(Client.id).where(Client.agent_user_id == user.id).limit(1))
        return client_res.scalar_one_or_none() is not None


async def create_agent_invite(
    agent_tg_id: int,
    *,
    ttl_hours: int = 72,
    uses_left: int = 1,
    target_client_id: int | None = None,
) -> AgentInvite | None:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None or user.role != UserRole.agent or user.tenant_id is None:
            return None
        token = secrets.token_urlsafe(24)
        inv = AgentInvite(
            tenant_id=user.tenant_id,
            agent_user_id=user.id,
            target_client_id=target_client_id,
            token=token,
            is_public=False,
            status=InviteStatus.active,
            uses_left=max(1, int(uses_left)),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=max(1, int(ttl_hours))),
        )
        session.add(inv)
        await session.commit()
        await session.refresh(inv)
        return inv


async def get_agent_invite_by_token(token: str) -> AgentInvite | None:
    async with get_session_maker()() as session:
        res = await session.execute(
            select(AgentInvite)
            .where(AgentInvite.token == token)
            .limit(1)
        )
        return res.scalar_one_or_none()


async def consume_agent_invite(
    token: str,
    client_tg_id: int,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    username: str | None = None,
) -> tuple[bool, str]:
    """
    Placeholder consume flow:
    - validates token lifecycle,
    - binds client user to invite tenant,
    - marks invite usage counters.
    """
    async with get_session_maker()() as session:
        inv_res = await session.execute(select(AgentInvite).where(AgentInvite.token == token).limit(1))
        inv = inv_res.scalar_one_or_none()
        if inv is None:
            return False, "invite_not_found"
        if inv.status != InviteStatus.active:
            return False, "invite_not_active"
        if inv.expires_at is not None and inv.expires_at <= datetime.now(timezone.utc):
            inv.status = InviteStatus.expired
            await session.commit()
            return False, "invite_expired"
        if inv.uses_left <= 0:
            inv.status = InviteStatus.used
            await session.commit()
            return False, "invite_depleted"

        user_res = await session.execute(select(User).where(User.tg_id == client_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            user = User(tg_id=client_tg_id, role=UserRole.client, tenant_id=inv.tenant_id)
            session.add(user)
            await session.flush()
        else:
            if user.tenant_id is None:
                user.tenant_id = inv.tenant_id
            elif user.tenant_id != inv.tenant_id:
                return False, "user_in_other_tenant"
            if user.role is None:
                user.role = UserRole.client

        # One Telegram client should be bound to only one CRM card.
        bound_res = await session.execute(select(Client).where(Client.source_user_id == user.id).limit(1))
        already_bound = bound_res.scalar_one_or_none()

        # If invite targets a concrete CRM client, bind exactly this client record.
        if inv.target_client_id is not None:
            target_res = await session.execute(
                select(Client).where(
                    Client.id == inv.target_client_id,
                    Client.agent_user_id == inv.agent_user_id,
                ).limit(1)
            )
            target_client = target_res.scalar_one_or_none()
            if target_client is None:
                return False, "target_client_not_found"
            if target_client.source_user_id is not None and target_client.source_user_id != user.id:
                return False, "target_client_already_bound"
            if already_bound is not None and already_bound.id != target_client.id:
                return False, "user_already_bound"
            target_client.source_user_id = user.id
        else:
            # Generic invite: ensure this invited user appears in agent's client base.
            existing_client_res = await session.execute(
                select(Client).where(
                    Client.agent_user_id == inv.agent_user_id,
                    Client.source_user_id == user.id,
                ).limit(1)
            )
            existing_client = existing_client_res.scalar_one_or_none()
            if already_bound is not None and existing_client is None:
                return False, "user_already_bound"
            if existing_client is None:
                session.add(
                    Client(
                        agent_user_id=inv.agent_user_id,
                        source_user_id=user.id,
                        full_name=_profile_display_name(first_name, last_name, username, user.tg_id),
                        phone=None,
                        email=None,
                    )
                )

        inv.uses_left -= 1
        inv.used_at = datetime.now(timezone.utc)
        inv.used_by_user_id = user.id
        if inv.uses_left <= 0:
            inv.status = InviteStatus.used
        await session.commit()
        return True, "ok"


async def create_client_bind_invite(agent_tg_id: int, client_id: int, *, ttl_hours: int = 72) -> AgentInvite | None:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = user_res.scalar_one_or_none()
        if agent is None or agent.role != UserRole.agent or agent.tenant_id is None:
            return None
        client_res = await session.execute(
            select(Client)
            .join(User, User.id == Client.agent_user_id)
            .where(
                Client.id == client_id,
                Client.agent_user_id == agent.id,
                User.tenant_id == agent.tenant_id,
            )
            .limit(1)
        )
        c = client_res.scalar_one_or_none()
        if c is None:
            return None
        if c.source_user_id is not None:
            return None
        return await create_agent_invite(
            agent_tg_id=agent_tg_id,
            ttl_hours=ttl_hours,
            uses_left=1,
            target_client_id=client_id,
        )


async def revoke_agent_invite(agent_tg_id: int, invite_id: int) -> bool:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None or user.role != UserRole.agent:
            return False
        res = await session.execute(
            select(AgentInvite)
            .where(
                AgentInvite.id == invite_id,
                AgentInvite.agent_user_id == user.id,
                AgentInvite.tenant_id == user.tenant_id,
            )
            .limit(1)
        )
        inv = res.scalar_one_or_none()
        if inv is None:
            return False
        inv.status = InviteStatus.revoked
        await session.commit()
        return True


async def get_or_create_public_agent_link(agent_tg_id: int) -> AgentInvite | None:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None or user.role != UserRole.agent or user.tenant_id is None:
            return None
        res = await session.execute(
            select(AgentInvite)
            .where(
                AgentInvite.agent_user_id == user.id,
                AgentInvite.tenant_id == user.tenant_id,
                AgentInvite.is_public.is_(True),
                AgentInvite.status == InviteStatus.active,
            )
            .order_by(AgentInvite.created_at.desc())
            .limit(1)
        )
        inv = res.scalar_one_or_none()
        if inv is not None:
            return inv
        token = secrets.token_urlsafe(24)
        inv = AgentInvite(
            tenant_id=user.tenant_id,
            agent_user_id=user.id,
            target_client_id=None,
            token=token,
            is_public=True,
            status=InviteStatus.active,
            uses_left=2_000_000_000,
            expires_at=None,
        )
        session.add(inv)
        await session.commit()
        await session.refresh(inv)
        return inv


async def regenerate_public_agent_link(agent_tg_id: int) -> AgentInvite | None:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None or user.role != UserRole.agent or user.tenant_id is None:
            return None
        old_res = await session.execute(
            select(AgentInvite).where(
                AgentInvite.agent_user_id == user.id,
                AgentInvite.tenant_id == user.tenant_id,
                AgentInvite.is_public.is_(True),
                AgentInvite.status == InviteStatus.active,
            )
        )
        for old in old_res.scalars().all():
            old.status = InviteStatus.revoked
        token = secrets.token_urlsafe(24)
        inv = AgentInvite(
            tenant_id=user.tenant_id,
            agent_user_id=user.id,
            target_client_id=None,
            token=token,
            is_public=True,
            status=InviteStatus.active,
            uses_left=2_000_000_000,
            expires_at=None,
        )
        session.add(inv)
        await session.commit()
        await session.refresh(inv)
        return inv


async def consume_public_agent_link(
    token: str,
    client_tg_id: int,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    username: str | None = None,
) -> tuple[bool, str]:
    async with get_session_maker()() as session:
        inv_res = await session.execute(
            select(AgentInvite)
            .where(
                AgentInvite.token == token,
                AgentInvite.is_public.is_(True),
            )
            .limit(1)
        )
        inv = inv_res.scalar_one_or_none()
        if inv is None:
            return False, "invite_not_found"
        if inv.status != InviteStatus.active:
            return False, "invite_not_active"

        user_res = await session.execute(select(User).where(User.tg_id == client_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            user = User(tg_id=client_tg_id, role=UserRole.client, tenant_id=inv.tenant_id)
            session.add(user)
            await session.flush()
        else:
            if user.tenant_id is None:
                user.tenant_id = inv.tenant_id
            elif user.tenant_id != inv.tenant_id:
                return False, "user_in_other_tenant"
            if user.role is None:
                user.role = UserRole.client

        bound_res = await session.execute(select(Client).where(Client.source_user_id == user.id).limit(1))
        already_bound = bound_res.scalar_one_or_none()

        existing_client_res = await session.execute(
            select(Client).where(
                Client.agent_user_id == inv.agent_user_id,
                Client.source_user_id == user.id,
            ).limit(1)
        )
        existing_client = existing_client_res.scalar_one_or_none()
        if already_bound is not None and existing_client is None:
            return False, "user_already_bound"
        if existing_client is None:
            session.add(
                Client(
                    agent_user_id=inv.agent_user_id,
                    source_user_id=user.id,
                    full_name=_profile_display_name(first_name, last_name, username, user.tg_id),
                    phone=None,
                    email=None,
                )
            )
        await session.commit()
        return True, "ok"


async def list_agent_invites(agent_tg_id: int, limit: int = 20) -> list[AgentInvite]:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None or user.role != UserRole.agent or user.tenant_id is None:
            return []
        res = await session.execute(
            select(AgentInvite)
            .where(
                AgentInvite.agent_user_id == user.id,
                AgentInvite.tenant_id == user.tenant_id,
            )
            .order_by(AgentInvite.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def list_invited_client_user_ids(agent_tg_id: int) -> set[int]:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None or user.role != UserRole.agent or user.tenant_id is None:
            return set()
        res = await session.execute(
            select(AgentInvite.used_by_user_id)
            .where(
                AgentInvite.agent_user_id == user.id,
                AgentInvite.tenant_id == user.tenant_id,
                AgentInvite.used_by_user_id.is_not(None),
            )
        )
        return {int(uid) for uid in res.scalars().all() if uid is not None}


async def create_application_for_client(tg_id: int, description: str | None = None) -> Application:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if user is None:
            tenant = await _get_default_tenant(session)
            user = User(tg_id=tg_id, role=UserRole.client, tenant_id=tenant.id)
            session.add(user)
            await session.flush()

        app = Application(client_user_id=user.id, status=ApplicationStatus.new, description=description)
        session.add(app)
        await session.commit()
        await session.refresh(app)
        return app


async def list_tenant_agent_tg_ids_for_client(client_tg_id: int) -> list[int]:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == client_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None or user.tenant_id is None:
            return []
        res = await session.execute(
            select(User.tg_id).where(
                User.role == UserRole.agent,
                User.tenant_id == user.tenant_id,
            )
        )
        return [int(x) for x in res.scalars().all()]


async def get_bound_client_profile(client_tg_id: int) -> tuple[str | None, str | None, str | None] | None:
    pair = await get_bound_agent_and_client_for_user(client_tg_id)
    if pair is None:
        return None
    _agent_tg_id, client_id = pair
    async with get_session_maker()() as session:
        c_res = await session.execute(select(Client).where(Client.id == client_id).limit(1))
        c = c_res.scalar_one_or_none()
        if c is None:
            return None
        return c.full_name, c.phone, c.email


async def list_bound_client_tg_for_agent(agent_tg_id: int) -> list[tuple[int, str]]:
    async with get_session_maker()() as session:
        agent_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = agent_res.scalar_one_or_none()
        if agent is None:
            return []
        res = await session.execute(
            select(User.tg_id, Client.full_name)
            .join(Client, Client.source_user_id == User.id)
            .where(
                Client.agent_user_id == agent.id,
                User.tenant_id == agent.tenant_id,
            )
        )
        return [(int(tg), str(name or "")) for tg, name in res.all()]


async def get_client_nearest_payment_or_contract_end(
    client_tg_id: int,
) -> tuple[str, dict] | None:
    """
    Returns:
      ("payment", {...}) or ("no_payments", {...}) or None (no contracts).
    """
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == client_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return None

        pay_res = await session.execute(
            select(Payment, Contract)
            .join(Contract, Contract.id == Payment.contract_id)
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(
                Client.source_user_id == user.id,
                User.tenant_id == user.tenant_id,
                Contract.status == ContractStatus.active,
                Payment.status == PaymentStatus.pending,
            )
            .order_by(Payment.due_date.asc())
            .limit(1)
        )
        row = pay_res.first()
        if row is not None:
            p, c = row
            return (
                "payment",
                {
                    "due_date": p.due_date,
                    "company": c.company,
                    "contract_number": c.contract_number,
                    "contract_kind": c.contract_kind,
                    "amount_minor": int(p.amount_minor),
                    "currency": c.currency,
                },
            )

        end_res = await session.execute(
            select(Contract)
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(
                Client.source_user_id == user.id,
                User.tenant_id == user.tenant_id,
                Contract.status == ContractStatus.active,
            )
            .order_by(Contract.end_date.asc())
            .limit(1)
        )
        ct = end_res.scalar_one_or_none()
        if ct is None:
            return None
        return (
            "no_payments",
            {
                "end_date": ct.end_date,
                "company": ct.company,
                "contract_number": ct.contract_number,
                "contract_kind": ct.contract_kind,
            },
        )


async def list_incoming_applications(agent_tg_id: int, limit: int = 20) -> list[Application]:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return []
        res = await session.execute(
            select(Application)
            .options(selectinload(Application.client), selectinload(Application.quote))
            .join(User, User.id == Application.client_user_id)
            .where(
                Application.status == ApplicationStatus.new,
                User.tenant_id == agent.tenant_id,
            )
            .order_by(Application.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def list_in_progress_applications(agent_tg_id: int, limit: int = 20) -> list[Application]:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return []
        res = await session.execute(
            select(Application)
            .options(selectinload(Application.client), selectinload(Application.quote))
            .join(User, User.id == Application.client_user_id)
            .where(
                Application.status == ApplicationStatus.in_progress,
                User.tenant_id == agent.tenant_id,
            )
            .order_by(Application.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def create_kasko_quote(
    tg_id: int,
    input_payload: dict,
    premium_byn: float,
    currency: str = "BYN",
) -> Quote:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if user is None:
            tenant = await _get_default_tenant(session)
            user = User(tg_id=tg_id, role=UserRole.client, tenant_id=tenant.id)
            session.add(user)
            await session.flush()

        premium_minor = int(round(premium_byn * 100))
        q = Quote(
            client_user_id=user.id,
            quote_type=QuoteType.kasko,
            input_json=json.dumps(input_payload, ensure_ascii=False),
            premium_amount=premium_minor,
            currency=currency,
        )
        session.add(q)
        await session.commit()
        await session.refresh(q)
        return q


async def create_property_quote(
    tg_id: int,
    input_payload: dict,
    premium_byn: float,
    currency: str = "BYN",
) -> Quote:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if user is None:
            tenant = await _get_default_tenant(session)
            user = User(tg_id=tg_id, role=UserRole.client, tenant_id=tenant.id)
            session.add(user)
            await session.flush()

        premium_minor = int(round(premium_byn * 100))
        q = Quote(
            client_user_id=user.id,
            quote_type=QuoteType.property,
            input_json=json.dumps(input_payload, ensure_ascii=False),
            premium_amount=premium_minor,
            currency=currency,
        )
        session.add(q)
        await session.commit()
        await session.refresh(q)
        return q


async def create_generic_quote(
    tg_id: int,
    quote_type: QuoteType,
    input_payload: dict,
    premium_byn: float,
    currency: str = "BYN",
) -> Quote:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if user is None:
            tenant = await _get_default_tenant(session)
            user = User(tg_id=tg_id, role=UserRole.client, tenant_id=tenant.id)
            session.add(user)
            await session.flush()

        premium_minor = int(round(premium_byn * 100))
        q = Quote(
            client_user_id=user.id,
            quote_type=quote_type,
            input_json=json.dumps(input_payload, ensure_ascii=False),
            premium_amount=premium_minor,
            currency=currency,
        )
        session.add(q)
        await session.commit()
        await session.refresh(q)
        return q


async def create_application_from_quote(tg_id: int, quote_id: int) -> Application:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if user is None:
            tenant = await _get_default_tenant(session)
            user = User(tg_id=tg_id, role=UserRole.client, tenant_id=tenant.id)
            session.add(user)
            await session.flush()

        q_res = await session.execute(select(Quote).where(Quote.id == quote_id))
        q = q_res.scalar_one_or_none()
        desc = f"Создано из расчёта quote_id={quote_id}"
        title = "Заявка по расчёту"
        if q is not None:
            try:
                payload = json.loads(q.input_json)
            except Exception:
                payload = {}
            name = payload.get("full_name")
            contact = payload.get("contact")
            if name or contact:
                parts: list[str] = []
                if name:
                    parts.append(f"Имя: {name}")
                if contact:
                    parts.append(f"Контакт: {contact}")
                desc = desc + " | " + ", ".join(parts)
            if q.quote_type == QuoteType.kasko:
                title = "КАСКО — заявка по расчёту"
            elif q.quote_type == QuoteType.property:
                title = "Имущество — заявка по расчёту"
            elif q.quote_type == QuoteType.cargo:
                title = "Грузы — заявка по расчёту"
            elif q.quote_type == QuoteType.accident:
                title = "Страховка за границу — заявка по расчёту"
            elif q.quote_type == QuoteType.expeditor:
                title = "Ответственность экспедитора — заявка по расчёту"
            elif q.quote_type == QuoteType.cmr:
                title = "CMR — заявка по расчёту"
            elif q.quote_type == QuoteType.dms:
                title = "ДМС — заявка по расчёту"
            elif q.quote_type == QuoteType.other:
                title = "Другой вид — заявка по расчёту"

        app = Application(
            client_user_id=user.id,
            status=ApplicationStatus.new,
            title=title,
            description=desc,
            quote_id=quote_id,
        )
        session.add(app)
        await session.commit()
        await session.refresh(app)
        return app


async def set_application_status(agent_tg_id: int, app_id: int, status: ApplicationStatus) -> Application | None:
    async with get_session_maker()() as session:
        agent_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = agent_res.scalar_one_or_none()
        if agent is None:
            return None
        res = await session.execute(
            select(Application)
            .join(User, User.id == Application.client_user_id)
            .where(Application.id == app_id, User.tenant_id == agent.tenant_id)
        )
        app = res.scalar_one_or_none()
        if app is None:
            return None
        app.status = status
        await session.commit()
        await session.refresh(app)
        return app


async def delete_application(agent_tg_id: int, app_id: int) -> Application | None:
    async with get_session_maker()() as session:
        agent_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = agent_res.scalar_one_or_none()
        if agent is None:
            return None
        res = await session.execute(
            select(Application)
            .join(User, User.id == Application.client_user_id)
            .where(Application.id == app_id, User.tenant_id == agent.tenant_id)
        )
        app = res.scalar_one_or_none()
        if app is None:
            return None
        await session.delete(app)
        await session.commit()
        return app


async def create_application_note(agent_tg_id: int, app_id: int, text_value: str) -> ApplicationNote | None:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return None
        app_res = await session.execute(
            select(Application)
            .join(User, User.id == Application.client_user_id)
            .where(Application.id == app_id, User.tenant_id == user.tenant_id)
        )
        app = app_res.scalar_one_or_none()
        if app is None or app.status != ApplicationStatus.in_progress:
            return None
        note = ApplicationNote(application_id=app_id, agent_user_id=user.id, text=text_value.strip())
        session.add(note)
        await session.commit()
        await session.refresh(note)
        return note


async def list_notes_for_application(agent_tg_id: int, app_id: int, limit: int = 20) -> list[ApplicationNote]:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return []
        app_res = await session.execute(
            select(Application)
            .join(User, User.id == Application.client_user_id)
            .where(Application.id == app_id, User.tenant_id == user.tenant_id)
        )
        app = app_res.scalar_one_or_none()
        if app is None:
            return []
        res = await session.execute(
            select(ApplicationNote)
            .where(ApplicationNote.agent_user_id == user.id, ApplicationNote.application_id == app_id)
            .order_by(ApplicationNote.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def list_notes_for_agent(agent_tg_id: int, limit: int = 50) -> list[ApplicationNote]:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return []
        res = await session.execute(
            select(ApplicationNote)
            .options(selectinload(ApplicationNote.application))
            .join(Application, Application.id == ApplicationNote.application_id)
            .join(User, User.id == Application.client_user_id)
            .where(
                ApplicationNote.agent_user_id == user.id,
                User.tenant_id == user.tenant_id,
            )
            .order_by(ApplicationNote.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def get_note_for_agent(agent_tg_id: int, note_id: int) -> ApplicationNote | None:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return None
        res = await session.execute(
            select(ApplicationNote)
            .options(selectinload(ApplicationNote.application))
            .join(Application, Application.id == ApplicationNote.application_id)
            .join(User, User.id == Application.client_user_id)
            .where(
                ApplicationNote.id == note_id,
                ApplicationNote.agent_user_id == user.id,
                User.tenant_id == user.tenant_id,
            )
        )
        return res.scalar_one_or_none()


async def delete_note_for_agent(agent_tg_id: int, note_id: int) -> bool:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return False
        res = await session.execute(
            select(ApplicationNote)
            .join(Application, Application.id == ApplicationNote.application_id)
            .join(User, User.id == Application.client_user_id)
            .where(
                ApplicationNote.id == note_id,
                ApplicationNote.agent_user_id == user.id,
                User.tenant_id == user.tenant_id,
            )
        )
        note = res.scalar_one_or_none()
        if note is None:
            return False
        await session.delete(note)
        await session.commit()
        return True


async def list_agent_companies_for_commission(agent_tg_id: int, limit: int = 200) -> list[str]:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return []
        contract_rows = await session.execute(
            select(func.distinct(Contract.company))
            .join(Client, Client.id == Contract.client_id)
            .where(Client.agent_user_id == user.id)
            .limit(limit)
        )
        commission_rows = await session.execute(
            select(func.distinct(AgentCommission.company))
            .where(AgentCommission.agent_user_id == user.id)
            .limit(limit)
        )
        companies = {
            str(v).strip()
            for v in list(contract_rows.scalars().all()) + list(commission_rows.scalars().all())
            if v is not None and str(v).strip()
        }
        return sorted(companies)


async def list_agent_contract_kinds_for_company(agent_tg_id: int, company: str, limit: int = 200) -> list[str]:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return []
        c = company.strip()
        contract_rows = await session.execute(
            select(func.distinct(Contract.contract_kind))
            .join(Client, Client.id == Contract.client_id)
            .where(Client.agent_user_id == user.id, Contract.company == c)
            .limit(limit)
        )
        commission_rows = await session.execute(
            select(func.distinct(AgentCommission.contract_kind))
            .where(AgentCommission.agent_user_id == user.id, AgentCommission.company == c)
            .limit(limit)
        )
        kinds = {
            str(v).strip()
            for v in list(contract_rows.scalars().all()) + list(commission_rows.scalars().all())
            if v is not None and str(v).strip()
        }
        return sorted(kinds)


async def upsert_agent_commission(
    agent_tg_id: int,
    company: str,
    contract_kind: str,
    percent_bp: int,
) -> AgentCommission | None:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return None
        c = company.strip()
        k = contract_kind.strip()
        res = await session.execute(
            select(AgentCommission).where(
                AgentCommission.agent_user_id == user.id,
                AgentCommission.company == c,
                AgentCommission.contract_kind == k,
            )
        )
        row = res.scalar_one_or_none()
        if row is None:
            row = AgentCommission(agent_user_id=user.id, company=c, contract_kind=k, percent_bp=percent_bp)
            session.add(row)
        else:
            row.percent_bp = percent_bp
        await session.commit()
        await session.refresh(row)
        return row


async def list_agent_commissions(agent_tg_id: int, limit: int = 2000) -> list[AgentCommission]:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return []
        res = await session.execute(
            select(AgentCommission)
            .where(AgentCommission.agent_user_id == user.id)
            .order_by(AgentCommission.company.asc(), AgentCommission.contract_kind.asc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def list_agent_company_kind_pairs(agent_tg_id: int, limit: int = 5000) -> list[tuple[str, str]]:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return []
        res = await session.execute(
            select(Contract.company, Contract.contract_kind)
            .join(Client, Client.id == Contract.client_id)
            .where(Client.agent_user_id == user.id)
            .distinct()
            .limit(limit)
        )
        pairs = [(str(c).strip(), str(k).strip()) for c, k in res.all() if c and k]
        return sorted(pairs, key=lambda x: (x[0], x[1]))


async def create_reminder(
    agent_tg_id: int,
    text_value: str,
    remind_at_utc: datetime,
    repeat: ReminderRepeat = ReminderRepeat.none,
    note_id: int | None = None,
) -> Reminder:
    if remind_at_utc.tzinfo is None:
        raise ValueError("remind_at_utc must be timezone-aware (UTC)")
    remind_at_utc = remind_at_utc.astimezone(timezone.utc)

    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = res.scalar_one_or_none()
        if user is None:
            tenant = await _get_default_tenant(session)
            user = User(tg_id=agent_tg_id, role=UserRole.agent, tenant_id=tenant.id)
            session.add(user)
            await session.flush()

        if note_id is not None:
            note_res = await session.execute(
                select(ApplicationNote)
                .join(Application, Application.id == ApplicationNote.application_id)
                .join(User, User.id == Application.client_user_id)
                .where(
                    ApplicationNote.id == note_id,
                    ApplicationNote.agent_user_id == user.id,
                    User.tenant_id == user.tenant_id,
                )
            )
            if note_res.scalar_one_or_none() is None:
                note_id = None

        r = Reminder(
            agent_user_id=user.id,
            text=text_value,
            remind_at=remind_at_utc,
            status=ReminderStatus.pending,
            repeat=repeat,
            note_id=note_id,
        )
        session.add(r)
        await session.commit()
        await session.refresh(r)
        return r


async def list_agent_reminders(agent_tg_id: int, limit: int = 10) -> list[Reminder]:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = res_u.scalar_one_or_none()
        if user is None:
            return []
        res = await session.execute(
            select(Reminder)
            .where(
                Reminder.agent_user_id == user.id,
                Reminder.status == ReminderStatus.pending,
            )
            .order_by(Reminder.remind_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def fetch_due_reminders(limit: int = 50) -> list[tuple[Reminder, int]]:
    """
    Returns list of (reminder, agent_tg_id).
    """
    now = datetime.now(timezone.utc)
    async with get_session_maker()() as session:
        res = await session.execute(
            select(Reminder, User.tg_id)
            .join(User, User.id == Reminder.agent_user_id)
            .where(Reminder.status == ReminderStatus.pending, Reminder.remind_at <= now)
            .order_by(Reminder.remind_at.asc())
            .limit(limit)
        )
        return list(res.all())


async def mark_reminder_sent(reminder_id: int) -> None:
    async with get_session_maker()() as session:
        res = await session.execute(select(Reminder).where(Reminder.id == reminder_id))
        r = res.scalar_one_or_none()
        if r is None:
            return
        r.status = ReminderStatus.sent
        r.sent_at = datetime.now(timezone.utc)
        await session.commit()


async def set_reminder_repeat(reminder_id: int, repeat: ReminderRepeat) -> None:
    async with get_session_maker()() as session:
        res = await session.execute(select(Reminder).where(Reminder.id == reminder_id))
        r = res.scalar_one_or_none()
        if r is None:
            return
        r.repeat = repeat
        await session.commit()


async def cancel_reminder(reminder_id: int) -> None:
    async with get_session_maker()() as session:
        res = await session.execute(select(Reminder).where(Reminder.id == reminder_id))
        r = res.scalar_one_or_none()
        if r is None:
            return
        r.status = ReminderStatus.cancelled
        await session.commit()


async def reschedule_recurring_reminder(reminder_id: int, next_remind_at_utc: datetime) -> None:
    async with get_session_maker()() as session:
        res = await session.execute(select(Reminder).where(Reminder.id == reminder_id))
        r = res.scalar_one_or_none()
        if r is None:
            return
        r.remind_at = next_remind_at_utc.astimezone(timezone.utc)
        r.sent_at = datetime.now(timezone.utc)
        r.status = ReminderStatus.pending
        await session.commit()


async def delete_reminder(reminder_id: int) -> None:
    async with get_session_maker()() as session:
        res = await session.execute(select(Reminder).where(Reminder.id == reminder_id))
        r = res.scalar_one_or_none()
        if r is None:
            return
        await session.delete(r)
        await session.commit()


async def update_reminder_datetime(reminder_id: int, remind_at_utc: datetime) -> None:
    if remind_at_utc.tzinfo is None:
        raise ValueError("remind_at_utc must be timezone-aware (UTC)")
    remind_at_utc = remind_at_utc.astimezone(timezone.utc)
    async with get_session_maker()() as session:
        res = await session.execute(select(Reminder).where(Reminder.id == reminder_id))
        r = res.scalar_one_or_none()
        if r is None:
            return
        r.remind_at = remind_at_utc
        r.status = ReminderStatus.pending
        r.sent_at = None
        await session.commit()


def _minor_from_byn(amount_byn: float) -> int:
    # BYN cents; avoids float accumulation when persisting.
    return int(round(amount_byn * 100))


def _byn_from_minor(amount_minor: int) -> float:
    return amount_minor / 100.0


async def create_client(
    agent_tg_id: int,
    full_name: str,
    phone: str | None,
    email: str | None,
    source_user_id: int | None = None,
) -> Client:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res.scalar_one_or_none()
        if agent is None:
            tenant = await _get_default_tenant(session)
            agent = User(tg_id=agent_tg_id, role=UserRole.agent, tenant_id=tenant.id)
            session.add(agent)
            await session.flush()

        c = Client(
            agent_user_id=agent.id,
            source_user_id=source_user_id,
            full_name=full_name,
            phone=phone,
            email=email,
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
        return c


async def list_clients(agent_tg_id: int, query: str | None = None, limit: int = 20) -> list[Client]:
    return await list_clients_page(agent_tg_id=agent_tg_id, query=query, limit=limit, offset=0)


async def list_clients_page(
    agent_tg_id: int,
    query: str | None = None,
    *,
    limit: int = 20,
    offset: int = 0,
    invited_only: bool = False,
) -> list[Client]:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return []

        stmt = (
            select(Client)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, User.tenant_id == agent.tenant_id)
        )
        if query:
            q = f"%{query.strip()}%"
            # ILIKE works in PostgreSQL; for other DBs this may behave differently.
            stmt = stmt.where(
                or_(
                    Client.full_name.ilike(q),
                    Client.phone.ilike(q),
                    Client.email.ilike(q),
                )
            )
        if invited_only:
            stmt = stmt.where(Client.source_user_id.is_not(None))

        stmt = stmt.order_by(Client.created_at.desc()).offset(offset).limit(limit)
        res = await session.execute(stmt)
        return list(res.scalars().all())


async def get_client(agent_tg_id: int, client_id: int) -> Client | None:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return None

        res = await session.execute(
            select(Client)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, Client.id == client_id, User.tenant_id == agent.tenant_id)
        )
        return res.scalar_one_or_none()


async def update_client(
    agent_tg_id: int,
    client_id: int,
    full_name: str,
    phone: str | None,
    email: str | None,
) -> Client | None:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return None

        res = await session.execute(
            select(Client)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, Client.id == client_id, User.tenant_id == agent.tenant_id)
        )
        c = res.scalar_one_or_none()
        if c is None:
            return None

        c.full_name = full_name
        c.phone = phone
        c.email = email
        await session.commit()
        await session.refresh(c)
        return c


async def create_contract_for_client(
    agent_tg_id: int,
    client_id: int,
    contract_number: str,
    company: str,
    contract_kind: str,
    start_date: date,
    end_date: date,
    total_amount_minor: int,
    insured_sum_minor: int | None,
    currency: str,
    initial_payment_amount_minor: int,
    initial_payment_due_date: date,
    payments: list[tuple[int, date]],  # (amount_minor, due_date)
    vehicle_description: str | None = None,
) -> Contract | None:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return None

        res_c = await session.execute(
            select(Client)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, Client.id == client_id, User.tenant_id == agent.tenant_id)
        )
        c = res_c.scalar_one_or_none()
        if c is None:
            return None

        contract = Contract(
            client_id=c.id,
            contract_number=contract_number,
            company=company,
            contract_kind=contract_kind,
            vehicle_description=vehicle_description,
            start_date=start_date,
            end_date=end_date,
            total_amount_minor=total_amount_minor,
            insured_sum_minor=insured_sum_minor,
            currency=currency,
        )
        session.add(contract)
        await session.flush()  # get contract.id

        # Initial payment is always immediately paid at contract conclusion.
        session.add(
            Payment(
                contract_id=contract.id,
                amount_minor=initial_payment_amount_minor,
                due_date=initial_payment_due_date,
                status=PaymentStatus.paid,
                paid_at=datetime.now(timezone.utc),
            )
        )

        for amount_minor, due_date in payments:
            session.add(
                Payment(
                    contract_id=contract.id,
                    amount_minor=amount_minor,
                    due_date=due_date,
                    status=PaymentStatus.pending,
                )
            )

        await session.commit()
        await session.refresh(contract)
        return contract


async def list_contracts_for_client(agent_tg_id: int, client_id: int, limit: int = 50) -> list[Contract]:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return []

        res = await session.execute(
            select(Contract)
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, Contract.client_id == client_id, User.tenant_id == agent.tenant_id)
            .order_by(Contract.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def list_contracts_for_client_user(client_tg_id: int, limit: int = 50) -> list[Contract]:
    """
    Contracts visible to Telegram client account bound via `clients.source_user_id`.
    """
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == client_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return []
        res = await session.execute(
            select(Contract)
            .options(selectinload(Contract.payments))
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(
                Client.source_user_id == user.id,
                User.tenant_id == user.tenant_id,
            )
            .order_by(Contract.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def get_bound_agent_and_client_for_user(client_tg_id: int) -> tuple[int, int] | None:
    """
    Returns (agent_tg_id, client_id) for Telegram user bound via invite.
    """
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == client_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return None
        # Prefer explicit latest invite consumption (deterministic source of truth).
        inv_res = await session.execute(
            select(AgentInvite)
            .where(
                AgentInvite.used_by_user_id == user.id,
                AgentInvite.tenant_id == user.tenant_id,
            )
            .order_by(AgentInvite.used_at.desc().nullslast(), AgentInvite.id.desc())
            .limit(1)
        )
        inv = inv_res.scalar_one_or_none()
        if inv is not None:
            if inv.target_client_id is not None:
                a_res = await session.execute(select(User.tg_id).where(User.id == inv.agent_user_id).limit(1))
                a_tg = a_res.scalar_one_or_none()
                if a_tg is not None:
                    return int(a_tg), int(inv.target_client_id)
            c_res = await session.execute(
                select(Client.id, User.tg_id)
                .join(User, User.id == Client.agent_user_id)
                .where(
                    Client.agent_user_id == inv.agent_user_id,
                    Client.source_user_id == user.id,
                    User.tenant_id == user.tenant_id,
                )
                .order_by(Client.created_at.desc())
                .limit(1)
            )
            c_row = c_res.first()
            if c_row is not None:
                return int(c_row[1]), int(c_row[0])
        # Fallback (legacy): latest bound client in tenant.
        res = await session.execute(
            select(User.tg_id, Client.id)
            .join(Client, Client.agent_user_id == User.id)
            .where(
                Client.source_user_id == user.id,
                User.tenant_id == user.tenant_id,
            )
            .order_by(Client.created_at.desc())
            .limit(1)
        )
        row = res.first()
        if row is None:
            return None
        return int(row[0]), int(row[1])


async def get_bound_agent_contact_for_client(
    client_tg_id: int,
) -> tuple[int, str | None, list[str], str | None, str | None, int, str | None, str | None] | None:
    """
    Returns:
      (agent_tg_id, agent_name, agent_phones, agent_email, agent_telegram, crm_client_id, crm_client_name, crm_client_phone)
    """
    pair = await get_bound_agent_and_client_for_user(client_tg_id)
    if pair is None:
        return None
    agent_tg_id, client_id = pair
    async with get_session_maker()() as session:
        agent_res = await session.execute(select(User).where(User.tg_id == agent_tg_id).limit(1))
        agent = agent_res.scalar_one_or_none()
        client_res = await session.execute(select(Client).where(Client.id == client_id).limit(1))
        c = client_res.scalar_one_or_none()
        if agent is None or c is None:
            return None
        phones: list[str] = []
        if agent.agent_contact_phones_json:
            try:
                parsed = json.loads(agent.agent_contact_phones_json)
                if isinstance(parsed, list):
                    phones = [str(x).strip() for x in parsed if str(x).strip()]
            except Exception:
                phones = []
        return (
            int(agent.tg_id),
            agent.display_name,
            phones,
            (agent.agent_contact_email or None),
            (agent.agent_contact_telegram or None),
            int(c.id),
            c.full_name,
            c.phone,
        )


async def update_bound_client_phone(client_tg_id: int, phone: str) -> bool:
    p = (phone or "").strip()
    if len(p) < 5:
        return False
    pair = await get_bound_agent_and_client_for_user(client_tg_id)
    if pair is None:
        return False
    _agent_tg_id, client_id = pair
    async with get_session_maker()() as session:
        c_res = await session.execute(select(Client).where(Client.id == client_id).limit(1))
        c = c_res.scalar_one_or_none()
        if c is None:
            return False
        c.phone = p
        await session.commit()
        return True


async def get_contract_bound_client_tg(agent_tg_id: int, contract_id: int) -> tuple[int, str] | None:
    """
    Returns (client_tg_id, client_full_name) for contract if CRM client is bound to Telegram user.
    """
    async with get_session_maker()() as session:
        agent_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = agent_res.scalar_one_or_none()
        if agent is None:
            return None
        res = await session.execute(
            select(User.tg_id, Client.full_name)
            .join(Client, Client.source_user_id == User.id)
            .join(Contract, Contract.client_id == Client.id)
            .where(
                Contract.id == contract_id,
                Client.agent_user_id == agent.id,
                User.tenant_id == agent.tenant_id,
            )
            .limit(1)
        )
        row = res.first()
        if row is None:
            return None
        return int(row[0]), str(row[1] or "")


async def get_contract_for_client_user(client_tg_id: int, contract_id: int) -> Contract | None:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == client_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return None
        res = await session.execute(
            select(Contract)
            .options(selectinload(Contract.payments))
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(
                Contract.id == contract_id,
                Client.source_user_id == user.id,
                User.tenant_id == user.tenant_id,
            )
            .limit(1)
        )
        ct = res.scalar_one_or_none()
        if ct is not None:
            ct.payments.sort(key=lambda p: p.due_date)
        return ct


async def list_contract_documents_for_client_user(client_tg_id: int, contract_id: int, limit: int = 10) -> list[ContractDocument]:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == client_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return []
        res = await session.execute(
            select(ContractDocument)
            .join(Contract, Contract.id == ContractDocument.contract_id)
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(
                ContractDocument.contract_id == contract_id,
                Client.source_user_id == user.id,
                User.tenant_id == user.tenant_id,
            )
            .order_by(ContractDocument.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def get_contract_document_for_client_user(client_tg_id: int, doc_id: int) -> ContractDocument | None:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == client_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return None
        res = await session.execute(
            select(ContractDocument)
            .join(Contract, Contract.id == ContractDocument.contract_id)
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(
                ContractDocument.id == doc_id,
                Client.source_user_id == user.id,
                User.tenant_id == user.tenant_id,
            )
            .limit(1)
        )
        return res.scalar_one_or_none()


async def create_contract_document_for_client_user(
    client_tg_id: int,
    contract_id: int,
    file_id: str,
    file_unique_id: str | None,
    caption: str | None,
) -> ContractDocument | None:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == client_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return None
        contract_res = await session.execute(
            select(Contract)
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(
                Contract.id == contract_id,
                Client.source_user_id == user.id,
                User.tenant_id == user.tenant_id,
            )
            .limit(1)
        )
        ct = contract_res.scalar_one_or_none()
        if ct is None:
            return None
        doc = ContractDocument(
            contract_id=ct.id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            caption=caption,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        return doc


async def list_client_documents_for_client_user(client_tg_id: int, limit: int = 20) -> list[ClientDocument]:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == client_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return []
        res = await session.execute(
            select(ClientDocument)
            .join(Client, Client.id == ClientDocument.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(
                Client.source_user_id == user.id,
                User.tenant_id == user.tenant_id,
            )
            .order_by(ClientDocument.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def get_client_document_for_client_user(client_tg_id: int, doc_id: int) -> ClientDocument | None:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == client_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return None
        res = await session.execute(
            select(ClientDocument)
            .join(Client, Client.id == ClientDocument.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(
                ClientDocument.id == doc_id,
                Client.source_user_id == user.id,
                User.tenant_id == user.tenant_id,
            )
            .limit(1)
        )
        return res.scalar_one_or_none()


async def create_client_document_for_client_user(
    client_tg_id: int,
    file_id: str,
    file_unique_id: str | None,
    caption: str | None,
) -> ClientDocument | None:
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == client_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return None
        client_res = await session.execute(
            select(Client)
            .join(User, User.id == Client.agent_user_id)
            .where(
                Client.source_user_id == user.id,
                User.tenant_id == user.tenant_id,
            )
            .order_by(Client.created_at.desc())
            .limit(1)
        )
        c = client_res.scalar_one_or_none()
        if c is None:
            return None
        doc = ClientDocument(
            client_id=c.id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            caption=caption,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        return doc


async def report_client_payment_with_adjustment(
    client_tg_id: int,
    contract_id: int,
    paid_date: date,
    amount_minor: int,
) -> tuple[int, str, int] | None:
    """
    Client reports payment via bot.
    Returns (agent_tg_id, contract_number, pending_left_count) on success.
    """
    if amount_minor <= 0:
        return None
    async with get_session_maker()() as session:
        user_res = await session.execute(select(User).where(User.tg_id == client_tg_id))
        user = user_res.scalar_one_or_none()
        if user is None:
            return None

        res = await session.execute(
            select(Contract, Client, User)
            .options(selectinload(Contract.payments))
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(
                Contract.id == contract_id,
                Client.source_user_id == user.id,
                User.tenant_id == user.tenant_id,
                Contract.status == ContractStatus.active,
            )
            .limit(1)
        )
        row = res.first()
        if row is None:
            return None
        ct, _crm_client, agent = row
        pending = sorted([p for p in ct.payments if p.status == PaymentStatus.pending], key=lambda p: p.due_date)
        if not pending:
            return None

        # First pending installment is considered paid.
        first_pending = pending[0]
        first_amount = int(first_pending.amount_minor)
        first_pending.status = PaymentStatus.paid
        first_pending.paid_at = datetime.combine(paid_date, datetime.min.time(), tzinfo=timezone.utc)
        delta = int(amount_minor) - first_amount

        # Adjust tail installments so schedule remains consistent with reported amount.
        tail = sorted([p for p in pending[1:] if p.status == PaymentStatus.pending], key=lambda p: p.due_date, reverse=True)
        if delta > 0 and tail:
            remaining = delta
            for p in tail:
                if remaining <= 0:
                    break
                take = min(int(p.amount_minor), remaining)
                p.amount_minor = int(p.amount_minor) - take
                remaining -= take
                if p.amount_minor <= 0:
                    await session.delete(p)
        elif delta < 0:
            debt = -delta
            if tail:
                tail[0].amount_minor = int(tail[0].amount_minor) + debt
            else:
                session.add(
                    Payment(
                        contract_id=ct.id,
                        amount_minor=debt,
                        due_date=ct.end_date,
                        status=PaymentStatus.pending,
                    )
                )

        await session.commit()
        # Recount pending.
        pending_count_res = await session.execute(
            select(func.count(Payment.id)).where(Payment.contract_id == ct.id, Payment.status == PaymentStatus.pending)
        )
        pending_left = int(pending_count_res.scalar() or 0)
        return int(agent.tg_id), str(ct.contract_number), pending_left


async def get_contract_detailed(agent_tg_id: int, contract_id: int) -> Contract | None:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return None

        res = await session.execute(
            select(Contract)
            .options(selectinload(Contract.payments))
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, Contract.id == contract_id, User.tenant_id == agent.tenant_id)
        )
        contract = res.scalar_one_or_none()
        if contract is None:
            return None
        # Ensure stable order for schedule rendering.
        contract.payments.sort(key=lambda p: p.due_date)
        return contract


async def search_contracts_by_number(agent_tg_id: int, query: str, limit: int = 10) -> list[Contract]:
    """Search contracts for this agent by substring of contract_number."""
    async with get_session_maker()() as session:
        agent_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = agent_res.scalar_one_or_none()
        if agent is None:
            return []

        q = f"%{query.strip()}%"
        res = await session.execute(
            select(Contract)
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, Contract.contract_number.ilike(q), User.tenant_id == agent.tenant_id)
            .order_by(Contract.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def mark_payment_paid(agent_tg_id: int, payment_id: int) -> int | None:
    """
    Mark a specific payment as paid (agent-scoped).
    Returns contract_id on success.
    """
    async with get_session_maker()() as session:
        agent_res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = agent_res.scalar_one_or_none()
        if agent is None:
            return None
        res = await session.execute(
            select(Payment)
            .join(Contract, Contract.id == Payment.contract_id)
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(
                User.tg_id == agent_tg_id,
                User.tenant_id == agent.tenant_id,
                Payment.id == payment_id,
                Payment.status == PaymentStatus.pending,
                Contract.status == ContractStatus.active,
            )
        )
        payment = res.scalar_one_or_none()
        if payment is None:
            return None

        payment.status = PaymentStatus.paid
        payment.paid_at = datetime.now(timezone.utc)
        await session.commit()
        return payment.contract_id


async def update_contract_for_client(
    agent_tg_id: int,
    contract_id: int,
    contract_number: str,
    company: str,
    contract_kind: str,
    start_date: date,
    end_date: date,
    total_amount_minor: int,
    insured_sum_minor: int | None,
    currency: str,
    initial_payment_amount_minor: int,
    initial_payment_due_date: date,
    payments: list[tuple[int, date]],
    vehicle_description: str | None = None,
) -> Contract | None:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return None

        contract_res = await session.execute(
            select(Contract)
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, Contract.id == contract_id, User.tenant_id == agent.tenant_id)
        )
        contract = contract_res.scalar_one_or_none()
        if contract is None:
            return None

        contract.contract_number = contract_number
        contract.company = company
        contract.contract_kind = contract_kind
        contract.vehicle_description = vehicle_description
        contract.start_date = start_date
        contract.end_date = end_date
        contract.total_amount_minor = total_amount_minor
        contract.insured_sum_minor = insured_sum_minor
        contract.currency = currency

        # Replace payment schedule.
        payments_res = await session.execute(select(Payment).where(Payment.contract_id == contract_id))
        existing = list(payments_res.scalars().all())
        for p in existing:
            await session.delete(p)
        await session.flush()

        session.add(
            Payment(
                contract_id=contract.id,
                amount_minor=initial_payment_amount_minor,
                due_date=initial_payment_due_date,
                status=PaymentStatus.paid,
                paid_at=datetime.now(timezone.utc),
            )
        )

        for amount_minor, due_date in payments:
            session.add(Payment(contract_id=contract.id, amount_minor=amount_minor, due_date=due_date, status=PaymentStatus.pending))

        await session.commit()
        await session.refresh(contract)
        return contract


async def terminate_contract(agent_tg_id: int, contract_id: int) -> bool:
    """Set contract status = terminated (agent-scoped)."""
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return False

        contract_res = await session.execute(
            select(Contract).join(Client, Client.id == Contract.client_id).join(User, User.id == Client.agent_user_id).where(
                Client.agent_user_id == agent.id,
                Contract.id == contract_id,
                User.tenant_id == agent.tenant_id,
            )
        )
        contract = contract_res.scalar_one_or_none()
        if contract is None:
            return False

        contract.status = ContractStatus.terminated
        await session.commit()
        return True


async def create_client_document(
    agent_tg_id: int,
    client_id: int,
    file_id: str,
    file_unique_id: str | None,
    caption: str | None,
) -> ClientDocument | None:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return None

        res_c = await session.execute(
            select(Client)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, Client.id == client_id, User.tenant_id == agent.tenant_id)
        )
        c = res_c.scalar_one_or_none()
        if c is None:
            return None

        doc = ClientDocument(
            client_id=c.id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            caption=caption,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        return doc


async def list_client_documents(
    agent_tg_id: int,
    client_id: int,
    limit: int = 10,
) -> list[ClientDocument]:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return []

        res = await session.execute(
            select(ClientDocument)
            .join(Client, Client.id == ClientDocument.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, ClientDocument.client_id == client_id, User.tenant_id == agent.tenant_id)
            .order_by(ClientDocument.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def get_client_document(agent_tg_id: int, doc_id: int) -> ClientDocument | None:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return None

        res = await session.execute(
            select(ClientDocument)
            .join(Client, Client.id == ClientDocument.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, ClientDocument.id == doc_id, User.tenant_id == agent.tenant_id)
        )
        return res.scalar_one_or_none()


async def create_contract_document(
    agent_tg_id: int,
    contract_id: int,
    file_id: str,
    file_unique_id: str | None,
    caption: str | None,
) -> ContractDocument | None:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return None

        res_c = await session.execute(
            select(Contract)
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, Contract.id == contract_id, User.tenant_id == agent.tenant_id)
        )
        contract = res_c.scalar_one_or_none()
        if contract is None:
            return None

        doc = ContractDocument(
            contract_id=contract.id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            caption=caption,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        return doc


async def list_contract_documents(
    agent_tg_id: int,
    contract_id: int,
    limit: int = 10,
) -> list[ContractDocument]:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return []

        res = await session.execute(
            select(ContractDocument)
            .join(Contract, Contract.id == ContractDocument.contract_id)
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, ContractDocument.contract_id == contract_id, User.tenant_id == agent.tenant_id)
            .order_by(ContractDocument.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def get_contract_document(agent_tg_id: int, doc_id: int) -> ContractDocument | None:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return None

        res = await session.execute(
            select(ContractDocument)
            .join(Contract, Contract.id == ContractDocument.contract_id)
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, ContractDocument.id == doc_id, User.tenant_id == agent.tenant_id)
        )
        return res.scalar_one_or_none()


async def contract_has_documents(agent_tg_id: int, contract_id: int) -> bool:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return False

        res = await session.execute(
            select(ContractDocument.id)
            .join(Contract, Contract.id == ContractDocument.contract_id)
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(
                Client.agent_user_id == agent.id,
                ContractDocument.contract_id == contract_id,
                User.tenant_id == agent.tenant_id,
            )
            .limit(1)
        )
        return res.scalar_one_or_none() is not None


async def delete_client_document(agent_tg_id: int, doc_id: int) -> bool:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return False

        res = await session.execute(
            select(ClientDocument)
            .join(Client, Client.id == ClientDocument.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, ClientDocument.id == doc_id, User.tenant_id == agent.tenant_id)
        )
        doc = res.scalar_one_or_none()
        if doc is None:
            return False
        await session.delete(doc)
        await session.commit()
        return True


async def delete_contract_document(agent_tg_id: int, doc_id: int) -> bool:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return False

        res = await session.execute(
            select(ContractDocument)
            .join(Contract, Contract.id == ContractDocument.contract_id)
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, ContractDocument.id == doc_id, User.tenant_id == agent.tenant_id)
        )
        doc = res.scalar_one_or_none()
        if doc is None:
            return False
        await session.delete(doc)
        await session.commit()
        return True


async def delete_contract(agent_tg_id: int, contract_id: int) -> bool:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return False

        res = await session.execute(
            select(Contract)
            .join(Client, Client.id == Contract.client_id)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, Contract.id == contract_id, User.tenant_id == agent.tenant_id)
        )
        contract = res.scalar_one_or_none()
        if contract is None:
            return False
        await session.delete(contract)
        await session.commit()
        return True


async def delete_client(agent_tg_id: int, client_id: int) -> bool:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return False

        res = await session.execute(
            select(Client)
            .join(User, User.id == Client.agent_user_id)
            .where(Client.agent_user_id == agent.id, Client.id == client_id, User.tenant_id == agent.tenant_id)
        )
        client = res.scalar_one_or_none()
        if client is None:
            return False
        await session.delete(client)
        await session.commit()
        return True
