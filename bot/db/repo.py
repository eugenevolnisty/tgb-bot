import json

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.db.base import get_session_maker
from bot.db.models import (
    Application,
    ApplicationStatus,
    Client,
    ClientDocument,
    ContractDocument,
    Contract,
    Payment,
    PaymentStatus,
    Quote,
    QuoteType,
    Reminder,
    ReminderRepeat,
    ReminderStatus,
    User,
    UserRole,
)

from sqlalchemy import or_
from datetime import date


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
        session.delete(r)
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
) -> Client:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res.scalar_one_or_none()
        if agent is None:
            agent = User(tg_id=agent_tg_id, role=UserRole.agent)
            session.add(agent)
            await session.flush()

        c = Client(agent_user_id=agent.id, full_name=full_name, phone=phone, email=email)
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
) -> list[Client]:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return []

        stmt = select(Client).where(Client.agent_user_id == agent.id)
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
            select(Client).where(Client.agent_user_id == agent.id, Client.id == client_id)
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
            select(Client).where(Client.agent_user_id == agent.id, Client.id == client_id)
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
    currency: str,
    payments: list[tuple[int, date]],  # (amount_minor, due_date)
    vehicle_description: str | None = None,
) -> Contract | None:
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res_u.scalar_one_or_none()
        if agent is None:
            return None

        res_c = await session.execute(select(Client).where(Client.agent_user_id == agent.id, Client.id == client_id))
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
            currency=currency,
        )
        session.add(contract)
        await session.flush()  # get contract.id

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
            .where(Client.agent_user_id == agent.id, Contract.client_id == client_id)
            .order_by(Contract.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())


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
            .where(Client.agent_user_id == agent.id, Contract.id == contract_id)
        )
        contract = res.scalar_one_or_none()
        if contract is None:
            return None
        # Ensure stable order for schedule rendering.
        contract.payments.sort(key=lambda p: p.due_date)
        return contract


async def update_contract_for_client(
    agent_tg_id: int,
    contract_id: int,
    contract_number: str,
    company: str,
    contract_kind: str,
    start_date: date,
    end_date: date,
    total_amount_minor: int,
    currency: str,
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
            .where(Client.agent_user_id == agent.id, Contract.id == contract_id)
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
        contract.currency = currency

        # Replace payment schedule.
        payments_res = await session.execute(select(Payment).where(Payment.contract_id == contract_id))
        existing = list(payments_res.scalars().all())
        for p in existing:
            session.delete(p)
        await session.flush()

        for amount_minor, due_date in payments:
            session.add(Payment(contract_id=contract.id, amount_minor=amount_minor, due_date=due_date, status=PaymentStatus.pending))

        await session.commit()
        await session.refresh(contract)
        return contract


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

        res_c = await session.execute(select(Client).where(Client.agent_user_id == agent.id, Client.id == client_id))
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
            .where(Client.agent_user_id == agent.id, ClientDocument.client_id == client_id)
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
            .where(Client.agent_user_id == agent.id, ClientDocument.id == doc_id)
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
            .where(Client.agent_user_id == agent.id, Contract.id == contract_id)
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
            .where(Client.agent_user_id == agent.id, ContractDocument.contract_id == contract_id)
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
            .where(Client.agent_user_id == agent.id, ContractDocument.id == doc_id)
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
            .where(
                Client.agent_user_id == agent.id,
                ContractDocument.contract_id == contract_id,
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
            .where(Client.agent_user_id == agent.id, ClientDocument.id == doc_id)
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
            .where(Client.agent_user_id == agent.id, ContractDocument.id == doc_id)
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
            .where(Client.agent_user_id == agent.id, Contract.id == contract_id)
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
            .where(Client.agent_user_id == agent.id, Client.id == client_id)
        )
        client = res.scalar_one_or_none()
        if client is None:
            return False
        await session.delete(client)
        await session.commit()
        return True
