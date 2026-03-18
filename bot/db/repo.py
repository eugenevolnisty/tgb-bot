import json

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.db.base import get_session_maker
from bot.db.models import Application, ApplicationStatus, Quote, QuoteType, Reminder, ReminderRepeat, ReminderStatus, User, UserRole


async def get_or_create_user(tg_id: int) -> User:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if user is not None:
            return user
        user = User(tg_id=tg_id)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def set_user_role(tg_id: int, role: UserRole) -> User:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if user is None:
            user = User(tg_id=tg_id, role=role)
            session.add(user)
        else:
            user.role = role
        await session.commit()
        await session.refresh(user)
        return user


async def create_application_for_client(tg_id: int, description: str | None = None) -> Application:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if user is None:
            user = User(tg_id=tg_id, role=UserRole.client)
            session.add(user)
            await session.flush()

        app = Application(client_user_id=user.id, status=ApplicationStatus.new, description=description)
        session.add(app)
        await session.commit()
        await session.refresh(app)
        return app


async def list_incoming_applications(limit: int = 20) -> list[Application]:
    async with get_session_maker()() as session:
        res = await session.execute(
            select(Application)
            .options(selectinload(Application.client), selectinload(Application.quote))
            .where(Application.status == ApplicationStatus.new)
            .order_by(Application.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


async def list_in_progress_applications(limit: int = 20) -> list[Application]:
    async with get_session_maker()() as session:
        res = await session.execute(
            select(Application)
            .options(selectinload(Application.client), selectinload(Application.quote))
            .where(Application.status == ApplicationStatus.in_progress)
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
            user = User(tg_id=tg_id, role=UserRole.client)
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
            user = User(tg_id=tg_id, role=UserRole.client)
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
            user = User(tg_id=tg_id, role=UserRole.client)
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
            user = User(tg_id=tg_id, role=UserRole.client)
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
                title = "Несчастные случаи — заявка по расчёту"
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


async def set_application_status(app_id: int, status: ApplicationStatus) -> Application | None:
    async with get_session_maker()() as session:
        res = await session.execute(select(Application).where(Application.id == app_id))
        app = res.scalar_one_or_none()
        if app is None:
            return None
        app.status = status
        await session.commit()
        await session.refresh(app)
        return app


async def create_reminder(
    agent_tg_id: int,
    text_value: str,
    remind_at_utc: datetime,
    repeat: ReminderRepeat = ReminderRepeat.none,
) -> Reminder:
    if remind_at_utc.tzinfo is None:
        raise ValueError("remind_at_utc must be timezone-aware (UTC)")
    remind_at_utc = remind_at_utc.astimezone(timezone.utc)

    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        user = res.scalar_one_or_none()
        if user is None:
            user = User(tg_id=agent_tg_id, role=UserRole.agent)
            session.add(user)
            await session.flush()

        r = Reminder(
            agent_user_id=user.id,
            text=text_value,
            remind_at=remind_at_utc,
            status=ReminderStatus.pending,
            repeat=repeat,
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
            .where(Reminder.agent_user_id == user.id)
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
