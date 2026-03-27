from __future__ import annotations

from datetime import date
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.db.models import ContractStatus, PaymentStatus, UserRole
from bot.db.repo import (
    create_client_document_for_client_user,
    create_contract_document_for_client_user,
    get_client_document_for_client_user,
    get_contract_document_for_client_user,
    get_contract_for_client_user,
    get_client_nearest_payment_or_contract_end,
    get_bound_agent_contact_for_client,
    get_or_create_user,
    list_client_documents_for_client_user,
    list_contract_documents_for_client_user,
    list_contracts_for_client_user,
    update_bound_client_phone,
    report_client_payment_with_adjustment,
)
from bot.keyboards import Btn, client_menu, to_main_menu_keyboard
from bot.services.datetime_parse import parse_date_ru

router = Router()


class ClientPaymentReport(StatesGroup):
    date = State()
    amount = State()
    photo = State()


class ClientDocsUpload(StatesGroup):
    photo = State()


class ClientContractDocsUpload(StatesGroup):
    photo = State()


class ClientAskAgent(StatesGroup):
    question = State()


class ClientCallAgent(StatesGroup):
    phone = State()


class AgentReplyQuestion(StatesGroup):
    text = State()


async def _ensure_client(message: Message) -> bool:
    user = await get_or_create_user(message.from_user.id)
    return user.role == UserRole.client


def _contract_status_text(status: ContractStatus) -> str:
    return "действует" if status == ContractStatus.active else "прекращен"


def _days_left_label(days_left: int) -> str:
    if days_left == 0:
        return "сегодня"
    if days_left == 1:
        return "завтра"
    if days_left > 1:
        return f"через {days_left} дн."
    return f"просрочен на {abs(days_left)} дн."


async def _render_client_contract_card(message: Message, client_tg_id: int, contract_id: int) -> None:
    ct = await get_contract_for_client_user(client_tg_id, contract_id)
    if ct is None:
        await message.answer("Договор не найден.", reply_markup=client_menu())
        return
    payments_lines: list[str] = []
    for p in sorted(ct.payments, key=lambda x: x.due_date):
        status = "оплачено" if p.status == PaymentStatus.paid else "ожидает оплаты"
        payments_lines.append(f"• {p.due_date:%d.%m.%Y}: {p.amount_minor/100:.2f} {ct.currency} — {status}")
    docs = await list_contract_documents_for_client_user(client_tg_id, ct.id, limit=10)
    text = (
        f"📄 Договор #{ct.id}\n"
        f"Номер: {ct.contract_number}\n"
        f"Компания: {ct.company}\n"
        f"Объект страхования: {ct.contract_kind}\n"
        f"Период: {ct.start_date:%d.%m.%Y} - {ct.end_date:%d.%m.%Y}\n"
        f"Статус: {_contract_status_text(ct.status)}\n"
        f"Страховой взнос (годовой): {ct.total_amount_minor/100:.2f} {ct.currency}\n\n"
        "График платежей:\n"
        + ("\n".join(payments_lines) if payments_lines else "— нет платежей")
    )
    if docs:
        text += "\n\n📎 Фото договора:\n" + "\n".join(f"• #{d.id}{' — ' + d.caption if d.caption else ''}" for d in docs[:10])
    pending_exists = any(p.status == PaymentStatus.pending for p in ct.payments) and ct.status == ContractStatus.active
    kb_rows: list[list[InlineKeyboardButton]] = []
    if pending_exists:
        kb_rows.append([InlineKeyboardButton(text="📨 Я платил(а)", callback_data=f"clpay:start:{ct.id}")])
    kb_rows.append([InlineKeyboardButton(text="📎 Добавить фото к договору", callback_data=f"clctdoc:add:{ct.id}")])
    for d in docs[:3]:
        kb_rows.append([InlineKeyboardButton(text=f"📷 Фото #{d.id}", callback_data=f"clctdoc:show:{d.id}")])
    if len(docs) > 3:
        kb_rows.append([InlineKeyboardButton(text="🖼 Открыть все фото", callback_data=f"clctdoc:all:{ct.id}")])
    kb_rows.append([InlineKeyboardButton(text="⬅️ К моим договорам", callback_data="clct:back")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.message(F.text == Btn.MY_CONTRACTS)
async def client_contracts(message: Message) -> None:
    if not await _ensure_client(message):
        return
    contracts = await list_contracts_for_client_user(message.from_user.id, limit=20)
    if not contracts:
        await message.answer("У вас пока нет привязанных договоров.", reply_markup=client_menu())
        return
    lines = ["📄 Мои договоры:"]
    rows: list[list[InlineKeyboardButton]] = []
    for c in contracts:
        status = _contract_status_text(c.status)
        pending = [p for p in c.payments if p.status == PaymentStatus.pending]
        nearest = min((p.due_date for p in pending), default=None)
        nearest_txt = f"{nearest:%d.%m.%Y}" if nearest else "—"
        lines.append(
            f"• #{c.id} {c.contract_number} | {c.company} | {c.contract_kind}\n"
            f"  Период: {c.start_date:%d.%m.%Y} - {c.end_date:%d.%m.%Y}, статус: {status}\n"
            f"  Ближайший взнос: {nearest_txt}, ожидающих: {len(pending)}"
        )
        rows.append([InlineKeyboardButton(text=f"Открыть {c.contract_number}", callback_data=f"clct:view:{c.id}")])
    await message.answer("\n".join(lines), reply_markup=client_menu())
    await message.answer("Открыть договор:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(F.text == Btn.MY_DOCS)
async def client_documents(message: Message, state: FSMContext) -> None:
    if not await _ensure_client(message):
        return
    await state.clear()
    docs = await list_client_documents_for_client_user(message.from_user.id, limit=20)
    lines = ["📎 Мои документы клиента:"]
    if not docs:
        lines.append("— пока нет")
    else:
        for d in docs:
            lines.append(f"• #{d.id}{' — ' + d.caption if d.caption else ''}")
    kb_rows: list[list[InlineKeyboardButton]] = [[InlineKeyboardButton(text="📎 Добавить фото документа", callback_data="cldoc:add")]]
    for d in docs[:10]:
        kb_rows.append([InlineKeyboardButton(text=f"📷 Открыть фото #{d.id}", callback_data=f"cldoc:show:{d.id}")])
    if len(docs) > 10:
        kb_rows.append([InlineKeyboardButton(text="🖼 Открыть все фото", callback_data="cldoc:all")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await message.answer("\n".join(lines), reply_markup=client_menu())
    await message.answer("Действия:", reply_markup=kb)


@router.message(F.text == Btn.CONTACT_AGENT)
async def client_contact_agent(message: Message) -> None:
    if not await _ensure_client(message):
        return
    info = await get_bound_agent_contact_for_client(message.from_user.id)
    if info is None:
        await message.answer("Агент пока не привязан.", reply_markup=client_menu())
        return
    _agent_tg_id, agent_name, phones, email, telegram, _client_id, _client_name, _client_phone = info
    lines = [f"📞 Связь с агентом: {agent_name or 'Ваш агент'}"]
    if phones:
        lines.append("Телефоны:")
        for p in phones:
            lines.append(f"- {p}")
    else:
        lines.append("Телефоны: —")
    lines.append(f"Telegram: @{telegram}" if telegram else "Telegram: —")
    lines.append(f"Email: {email or '—'}")
    kb = InlineKeyboardMarkup(
        inline_keyboard=(
            [
                [InlineKeyboardButton(text="💬 Открыть Telegram агента", url=f"https://t.me/{telegram}")]
            ]
            if telegram
            else []
        )
        + [
            [InlineKeyboardButton(text="❓ Задать вопрос агенту", callback_data="clagent:ask")],
            [InlineKeyboardButton(text="📲 Попросить агента позвонить мне", callback_data="clagent:call")],
        ]
    )
    await message.answer("\n".join(lines), reply_markup=kb)


@router.message(F.text == Btn.NEXT_PAYMENT)
async def client_next_payment(message: Message) -> None:
    if not await _ensure_client(message):
        return
    info = await get_client_nearest_payment_or_contract_end(message.from_user.id)
    if info is None:
        await message.answer("У вас пока нет привязанных договоров.", reply_markup=client_menu())
        return
    kind, payload = info
    if kind == "payment":
        due = payload["due_date"]
        days_left = (due - date.today()).days
        days_txt = _days_left_label(days_left)
        await message.answer(
            "⏳ Ближайший взнос:\n"
            f"Дата платежа: {due:%d.%m.%Y} ({days_txt})\n"
            f"Компания: {payload['company']}\n"
            f"Договор №: {payload['contract_number']}\n"
            f"Объект: {payload['contract_kind']}\n"
            f"Сумма: {payload['amount_minor']/100:.2f} {payload['currency']}",
            reply_markup=client_menu(),
        )
        return
    await message.answer(
        "Платежей нет (все оплачены).\n"
        f"Ближайшее окончание договора: {payload['end_date']:%d.%m.%Y}\n"
        f"Договор №: {payload['contract_number']}\n"
        f"Компания: {payload['company']}\n"
        f"Объект: {payload['contract_kind']}",
        reply_markup=client_menu(),
    )


@router.callback_query(F.data.startswith("clct:view:"))
async def client_view_contract(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        contract_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    await _render_client_contract_card(callback.message, callback.from_user.id, contract_id)
    await callback.answer()


@router.callback_query(F.data == "clct:back")
async def client_contracts_back(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    contracts = await list_contracts_for_client_user(callback.from_user.id, limit=20)
    if not contracts:
        await callback.message.answer("У вас пока нет привязанных договоров.", reply_markup=client_menu())
        await callback.answer()
        return
    lines = ["📄 Мои договоры:"]
    rows: list[list[InlineKeyboardButton]] = []
    for c in contracts:
        status = _contract_status_text(c.status)
        pending = [p for p in c.payments if p.status == PaymentStatus.pending]
        nearest = min((p.due_date for p in pending), default=None)
        nearest_txt = f"{nearest:%d.%m.%Y}" if nearest else "—"
        lines.append(
            f"• #{c.id} {c.contract_number} | {c.company} | {c.contract_kind}\n"
            f"  Период: {c.start_date:%d.%m.%Y} - {c.end_date:%d.%m.%Y}, статус: {status}\n"
            f"  Ближайший взнос: {nearest_txt}, ожидающих: {len(pending)}"
        )
        rows.append([InlineKeyboardButton(text=f"Открыть {c.contract_number}", callback_data=f"clct:view:{c.id}")])
    await callback.message.answer("\n".join(lines), reply_markup=client_menu())
    await callback.message.answer("Открыть договор:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "cldoc:add")
async def client_docs_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await state.clear()
    await state.set_state(ClientDocsUpload.photo)
    await callback.message.answer("Отправьте фото документа и подпись (опционально).", reply_markup=to_main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "clagent:ask")
async def client_ask_agent_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await state.clear()
    await state.set_state(ClientAskAgent.question)
    await callback.message.answer("Напишите ваш вопрос агенту:", reply_markup=to_main_menu_keyboard())
    await callback.answer()


@router.message(ClientAskAgent.question)
async def client_ask_agent_send(message: Message, state: FSMContext) -> None:
    if not await _ensure_client(message):
        return
    q = (message.text or "").strip()
    if len(q) < 3:
        await message.answer("Вопрос слишком короткий.")
        return
    info = await get_bound_agent_contact_for_client(message.from_user.id)
    if info is None:
        await state.clear()
        await message.answer("Агент пока не привязан.", reply_markup=client_menu())
        return
    agent_tg_id, _agent_name, _phones, _email, _telegram, _client_id, client_name, client_phone = info
    client_open_btn: list[list[InlineKeyboardButton]] = []
    if _client_id:
        client_open_btn = [
            [InlineKeyboardButton(text=f"👤 Открыть клиента #{_client_id}", callback_data=f"client:open:{_client_id}")]
        ]
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Ответить клиенту", callback_data=f"agentq:reply:{message.from_user.id}")],
            [InlineKeyboardButton(text="⏰ Напомнить мне об этом", callback_data=f"agentq:remind:{message.from_user.id}")],
        ]
        + client_open_btn
    )
    text = (
        "❓ Новый вопрос от клиента\n"
        f"Клиент: {client_name or '—'}\n"
        f"Телефон: {client_phone or '—'}\n"
        f"tg_id: {message.from_user.id}\n\n"
        f"Вопрос: {q}"
    )
    try:
        await message.bot.send_message(agent_tg_id, text, reply_markup=kb)
    except Exception:
        pass
    await state.clear()
    await message.answer("✅ Вопрос отправлен агенту.", reply_markup=client_menu())


@router.callback_query(F.data == "clagent:call")
async def client_call_agent_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    info = await get_bound_agent_contact_for_client(callback.from_user.id)
    if info is None:
        await callback.answer("Агент пока не привязан.", show_alert=True)
        return
    agent_tg_id, _agent_name, _phones, _email, _telegram, _client_id, client_name, client_phone = info
    if not client_phone:
        await state.clear()
        await state.set_state(ClientCallAgent.phone)
        await callback.message.answer("Укажите ваш номер телефона для звонка:")
        await callback.answer()
        return
    text = (
        "📲 Клиент просит перезвонить\n"
        f"Клиент: {client_name or '—'}\n"
        f"Телефон: {client_phone}\n"
        f"tg_id: {callback.from_user.id}"
    )
    try:
        await callback.bot.send_message(agent_tg_id, text)
    except Exception:
        pass
    await callback.answer("Запрос отправлен")


@router.callback_query(F.data == "clagent:nextpay")
async def client_next_payment_from_contact(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    info = await get_client_nearest_payment_or_contract_end(callback.from_user.id)
    if info is None:
        await callback.message.answer("У вас пока нет привязанных договоров.", reply_markup=client_menu())
        await callback.answer()
        return
    kind, payload = info
    if kind == "payment":
        due = payload["due_date"]
        days_left = (due - date.today()).days
        days_txt = _days_left_label(days_left)
        await callback.message.answer(
            "⏳ Ближайший взнос:\n"
            f"Дата платежа: {due:%d.%m.%Y} ({days_txt})\n"
            f"Компания: {payload['company']}\n"
            f"Договор №: {payload['contract_number']}\n"
            f"Объект: {payload['contract_kind']}\n"
            f"Сумма: {payload['amount_minor']/100:.2f} {payload['currency']}",
            reply_markup=client_menu(),
        )
        await callback.answer()
        return
    await callback.message.answer(
        "Платежей нет (все оплачены).\n"
        f"Ближайшее окончание договора: {payload['end_date']:%d.%m.%Y}\n"
        f"Договор №: {payload['contract_number']}\n"
        f"Компания: {payload['company']}\n"
        f"Объект: {payload['contract_kind']}",
        reply_markup=client_menu(),
    )
    await callback.answer()


@router.message(ClientCallAgent.phone)
async def client_call_agent_phone(message: Message, state: FSMContext) -> None:
    if not await _ensure_client(message):
        return
    phone = (message.text or "").strip()
    if len(phone) < 5:
        await message.answer("Введите корректный номер телефона.")
        return
    await update_bound_client_phone(message.from_user.id, phone)
    info = await get_bound_agent_contact_for_client(message.from_user.id)
    if info is not None:
        agent_tg_id, _agent_name, _phones, _email, _telegram, _client_id, client_name, _client_phone = info
        text = (
            "📲 Клиент просит перезвонить\n"
            f"Клиент: {client_name or '—'}\n"
            f"Телефон: {phone}\n"
            f"tg_id: {message.from_user.id}"
        )
        try:
            await message.bot.send_message(agent_tg_id, text)
        except Exception:
            pass
    await state.clear()
    await message.answer("✅ Запрос отправлен агенту.", reply_markup=client_menu())


@router.callback_query(F.data.startswith("agentq:reply:"))
async def agent_reply_question_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        client_tg_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный id", show_alert=True)
        return
    await state.clear()
    await state.update_data(agentq_client_tg_id=client_tg_id)
    await state.set_state(AgentReplyQuestion.text)
    await callback.message.answer("Введите ответ клиенту:")
    await callback.answer()


@router.message(AgentReplyQuestion.text)
async def agent_reply_question_send(message: Message, state: FSMContext) -> None:
    user = await get_or_create_user(message.from_user.id)
    if user.role != UserRole.agent:
        return
    data = await state.get_data()
    client_tg_id = int(data.get("agentq_client_tg_id") or 0)
    if client_tg_id <= 0:
        await state.clear()
        return
    ans = (message.text or "").strip()
    if len(ans) < 2:
        await message.answer("Ответ слишком короткий.")
        return
    try:
        await message.bot.send_message(client_tg_id, f"✉️ Ответ агента:\n{ans}")
    except Exception:
        await message.answer("Не удалось доставить ответ клиенту.")
        await state.clear()
        return
    await state.clear()
    await message.answer("✅ Ответ отправлен клиенту.")


@router.callback_query(F.data.startswith("agentq:remind:"))
async def agent_question_remind(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    user = await get_or_create_user(callback.from_user.id)
    if user.role != UserRole.agent:
        await callback.answer("Недоступно", show_alert=True)
        return
    text = (callback.message.text or "").strip()
    q_line = ""
    for ln in text.splitlines():
        if ln.startswith("Вопрос:"):
            q_line = ln
            break
    remind_at = datetime.now(timezone.utc) + timedelta(hours=1)
    from bot.db.repo import create_reminder  # local import to avoid cycles
    await create_reminder(callback.from_user.id, text=f"Перезвон/ответ клиенту. {q_line}", remind_at_utc=remind_at)
    await callback.answer("Напоминание создано на +1 час")


@router.callback_query(F.data.startswith("cldoc:show:"))
async def client_docs_show(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        doc_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    doc = await get_client_document_for_client_user(callback.from_user.id, doc_id)
    if doc is None:
        await callback.answer("Документ не найден", show_alert=True)
        return
    await callback.message.answer_photo(
        photo=doc.file_id,
        caption=(doc.caption or f"Документ #{doc.id}"),
    )
    await callback.answer()


@router.callback_query(F.data == "cldoc:all")
async def client_docs_show_all(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    docs = await list_client_documents_for_client_user(callback.from_user.id, limit=100)
    if not docs:
        await callback.answer("Документы не найдены", show_alert=True)
        return
    for d in docs:
        await callback.message.answer_photo(
            photo=d.file_id,
            caption=(d.caption or f"Документ #{d.id}"),
        )
    await callback.answer()


@router.message(ClientDocsUpload.photo)
async def client_docs_add_photo(message: Message, state: FSMContext) -> None:
    if not await _ensure_client(message):
        return
    if not message.photo:
        await message.answer("Нужно отправить именно фото.")
        return
    ph = message.photo[-1]
    doc = await create_client_document_for_client_user(
        message.from_user.id,
        file_id=ph.file_id,
        file_unique_id=ph.file_unique_id,
        caption=(message.caption or None),
    )
    await state.clear()
    if doc is None:
        await message.answer("Не удалось сохранить фото документа.", reply_markup=client_menu())
        return
    await message.answer(f"✅ Фото документа сохранено: #{doc.id}", reply_markup=client_menu())


@router.callback_query(F.data.startswith("clctdoc:add:"))
async def client_contract_doc_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        contract_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    await state.clear()
    await state.update_data(cl_contract_id=contract_id)
    await state.set_state(ClientContractDocsUpload.photo)
    await callback.message.answer("Отправьте фото для этого договора и подпись (опционально).", reply_markup=to_main_menu_keyboard())
    await callback.answer()


@router.message(ClientContractDocsUpload.photo)
async def client_contract_doc_add_photo(message: Message, state: FSMContext) -> None:
    if not await _ensure_client(message):
        return
    data = await state.get_data()
    contract_id = int(data.get("cl_contract_id") or 0)
    if contract_id <= 0:
        await state.clear()
        await message.answer("Контекст договора потерян.", reply_markup=client_menu())
        return
    if not message.photo:
        await message.answer("Нужно отправить именно фото.")
        return
    ph = message.photo[-1]
    doc = await create_contract_document_for_client_user(
        message.from_user.id,
        contract_id=contract_id,
        file_id=ph.file_id,
        file_unique_id=ph.file_unique_id,
        caption=(message.caption or None),
    )
    await state.clear()
    if doc is None:
        await message.answer("Не удалось сохранить фото к договору.", reply_markup=client_menu())
        return
    await message.answer(f"✅ Фото к договору сохранено: #{doc.id}", reply_markup=client_menu())


@router.callback_query(F.data.startswith("clctdoc:show:"))
async def client_contract_doc_show(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        doc_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    doc = await get_contract_document_for_client_user(callback.from_user.id, doc_id)
    if doc is None:
        await callback.answer("Фото не найдено", show_alert=True)
        return
    await callback.message.answer_photo(
        photo=doc.file_id,
        caption=(doc.caption or f"Фото договора #{doc.contract_id}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("clctdoc:all:"))
async def client_contract_doc_show_all(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        contract_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    docs = await list_contract_documents_for_client_user(callback.from_user.id, contract_id, limit=50)
    if not docs:
        await callback.answer("Фото не найдены", show_alert=True)
        return
    for d in docs:
        await callback.message.answer_photo(
            photo=d.file_id,
            caption=(d.caption or f"Фото договора #{contract_id}"),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("clpay:start:"))
async def client_payment_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        contract_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    await state.clear()
    await state.update_data(pay_contract_id=contract_id)
    await state.set_state(ClientPaymentReport.date)
    await callback.message.answer("Введите дату оплаты (например: 26.03.2026).", reply_markup=to_main_menu_keyboard())
    await callback.answer()


@router.message(ClientPaymentReport.date)
async def client_payment_date(message: Message, state: FSMContext) -> None:
    if not await _ensure_client(message):
        return
    d = parse_date_ru(message.text or "", today=date.today())
    if d is None:
        await message.answer("Не понял дату. Пример: 26.03.2026")
        return
    await state.update_data(pay_date_iso=d.target_date.isoformat())
    await state.set_state(ClientPaymentReport.amount)
    await message.answer("Введите сумму оплаты, например: 150.00")


@router.message(ClientPaymentReport.amount)
async def client_payment_amount(message: Message, state: FSMContext) -> None:
    if not await _ensure_client(message):
        return
    raw = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        val = float(raw)
    except Exception:
        await message.answer("Введите сумму числом, например: 150.00")
        return
    if val <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return
    await state.update_data(pay_amount_minor=int(round(val * 100)))
    await state.set_state(ClientPaymentReport.photo)
    await message.answer("Прикрепите скрин/фото оплаты (можно с подписью).")


@router.message(ClientPaymentReport.photo)
async def client_payment_photo(message: Message, state: FSMContext) -> None:
    if not await _ensure_client(message):
        return
    data = await state.get_data()
    contract_id = int(data.get("pay_contract_id") or 0)
    amount_minor = int(data.get("pay_amount_minor") or 0)
    paid_date_iso = str(data.get("pay_date_iso") or "")
    if contract_id <= 0 or amount_minor <= 0 or not paid_date_iso:
        await state.clear()
        await message.answer("Контекст оплаты потерян. Начните заново.", reply_markup=client_menu())
        return
    if not message.photo:
        await message.answer("Нужно отправить фото/скрин оплаты.")
        return
    paid_date = date.fromisoformat(paid_date_iso)
    result = await report_client_payment_with_adjustment(
        message.from_user.id,
        contract_id=contract_id,
        paid_date=paid_date,
        amount_minor=amount_minor,
    )
    if result is None:
        await state.clear()
        await message.answer("Не удалось обработать оплату. Проверьте договор/график.", reply_markup=client_menu())
        return
    agent_tg_id, contract_number, pending_left = result
    ph = message.photo[-1]
    caption = (message.caption or "").strip()
    proof_caption = f"Оплата от клиента: {paid_date:%d.%m.%Y}, сумма {amount_minor/100:.2f}. {caption}".strip()
    doc = await create_contract_document_for_client_user(
        message.from_user.id,
        contract_id=contract_id,
        file_id=ph.file_id,
        file_unique_id=ph.file_unique_id,
        caption=proof_caption,
    )
    await state.clear()
    await message.answer("✅ Спасибо! Оплата отправлена агенту и учтена в графике.", reply_markup=client_menu())
    try:
        await message.bot.send_photo(
            agent_tg_id,
            photo=ph.file_id,
            caption=(
                f"💸 Клиент сообщил об оплате\n"
                f"Договор: {contract_number}\n"
                f"Дата: {paid_date:%d.%m.%Y}\n"
                f"Сумма: {amount_minor/100:.2f}\n"
                f"Осталось ожидающих взносов: {pending_left}"
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Открыть договор", callback_data=f"client:view_contract:{contract_id}")],
                    *(
                        [[InlineKeyboardButton(text=f"Открыть фото #{doc.id}", callback_data=f"contract:show_doc:{doc.id}")]]
                        if doc is not None
                        else []
                    ),
                ]
            ),
        )
    except Exception:
        pass
