from __future__ import annotations

import calendar
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.db.models import Client, ContractStatus, PaymentStatus, UserRole
from bot.db.repo import (
    create_client_bind_invite,
    create_client,
    create_client_document,
    create_contract_document,
    create_contract_for_client,
    delete_client,
    delete_contract,
    delete_client_document,
    delete_contract_document,
    get_client,
    get_client_document,
    get_contract_document,
    get_contract_detailed,
    get_contract_bound_client_tg,
    list_clients,
    list_clients_page,
    list_invited_client_user_ids,
    list_contracts_for_client,
    list_client_documents,
    list_contract_documents,
    contract_has_documents,
    terminate_contract,
    search_contracts_by_number,
    mark_payment_paid,
    update_client,
    update_contract_for_client,
)
from bot.keyboards import Btn, agent_menu, to_main_menu_keyboard
from bot.services.datetime_parse import parse_date_ru

router = Router()

# In-memory mapping: last shown ordered list of clients per agent.
# Allows: agent opens "clients list" and then sends "1/2/3..." to open card.
_LAST_CLIENTS_BY_AGENT: dict[int, list[int]] = {}
_CLIENTS_FILTER_BY_AGENT: dict[int, str] = {}

# Used to refresh UI after deletion:
# when user opens a doc photo from the client/contract card,
# we remember the card message_id so after delete we can remove stale UI.
_CLIENT_DOC_CARD_MSG: dict[tuple[int, int], tuple[int, int]] = {}
# (agent_tg_id, doc_id) -> (client_id, card_message_id)

_CONTRACT_DOC_CARD_MSG: dict[tuple[int, int], tuple[int, int]] = {}
# (agent_tg_id, doc_id) -> (contract_id, card_message_id)

# Rolling-clean storage for transient flow prompts.
_FLOW_LAST_PROMPT_MSG: dict[tuple[int, str], int] = {}


async def _clear_flow_prompt(message: Message, flow_key: str) -> None:
    key = (message.chat.id, flow_key)
    msg_id = _FLOW_LAST_PROMPT_MSG.pop(key, None)
    if msg_id is None:
        return
    try:
        await message.bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
    except Exception:
        pass


async def _flow_answer(message: Message, flow_key: str, text: str, **kwargs) -> Message:
    await _clear_flow_prompt(message, flow_key)
    sent = await message.answer(text, **kwargs)
    _FLOW_LAST_PROMPT_MSG[(message.chat.id, flow_key)] = sent.message_id
    return sent


async def _delete_user_button_message(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


async def _set_reply_keyboard_silent_message(message: Message, *, text: str, reply_markup) -> None:
    await message.answer(text, reply_markup=reply_markup)


async def _set_reply_keyboard_silent_callback(callback: CallbackQuery, *, text: str, reply_markup) -> None:
    if callback.message is None:
        return
    await callback.message.answer(text, reply_markup=reply_markup)


async def _ensure_agent(message: Message) -> bool:
    # Agent identity is stored in `users` table with role `agent`.
    # We keep the same behavior as other handlers.
    from bot.db.repo import get_or_create_user

    user = await get_or_create_user(message.from_user.id)
    return user.role == UserRole.agent


async def _ensure_agent_tg(tg_id: int) -> bool:
    from bot.db.repo import get_or_create_user

    user = await get_or_create_user(tg_id)
    return user.role == UserRole.agent


class ClientsMenu:
    ADD = "➕ Добавить клиента"
    SEARCH = "🔎 Поиск клиентов"
    BACK = "⬅️ Назад"


def clients_menu_keyboard() -> "aiogram.types.ReplyKeyboardMarkup":
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ClientsMenu.ADD)],
            [KeyboardButton(text=ClientsMenu.SEARCH)],
            [KeyboardButton(text=ClientsMenu.BACK)],
            [KeyboardButton(text=Btn.MAIN_MENU)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Клиенты",
    )


async def open_clients_menu(
    message: Message,
    *,
    limit: int = 20,
    offset: int = 0,
    agent_tg_id: int | None = None,
    filter_mode: str | None = None,
) -> None:
    tg_id = agent_tg_id if agent_tg_id is not None else message.from_user.id
    if not await _ensure_agent_tg(tg_id):
        return
    mode = filter_mode or _CLIENTS_FILTER_BY_AGENT.get(tg_id, "all")
    if mode not in {"all", "invited"}:
        mode = "all"
    _CLIENTS_FILTER_BY_AGENT[tg_id] = mode
    # Fetch one extra row to detect next page.
    items_page = await list_clients_page(
        tg_id,
        limit=limit + 1,
        offset=offset,
        invited_only=(mode == "invited"),
    )
    if not items_page:
        await message.answer("Пока клиентов нет. Добавьте первого.", reply_markup=clients_menu_keyboard())
        return

    items = items_page[:limit]
    invited_user_ids = await list_invited_client_user_ids(tg_id)
    has_next = len(items_page) > limit
    has_prev = offset > 0

    lines = ["📋 Клиенты:"]
    lines.append("Фильтр: только по инвайту" if mode == "invited" else "Фильтр: все")
    # Store client ids in the same order we show them.
    _LAST_CLIENTS_BY_AGENT[tg_id] = [c.id for c in items]
    for i, c in enumerate(items, start=1):
        phone = f" • {c.phone}" if c.phone else ""
        invited_mark = " 🔗" if (getattr(c, "source_user_id", None) in invited_user_ids) else ""
        lines.append(f"{i}) {c.full_name}{phone}{invited_mark}")

    await message.answer("\n".join(lines), reply_markup=clients_menu_keyboard())

    kb_rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=("✅ Все" if mode == "all" else "Все"),
                callback_data="clients:filter:all",
            ),
            InlineKeyboardButton(
                text=("✅ По инвайту" if mode == "invited" else "По инвайту"),
                callback_data="clients:filter:invited",
            ),
        ]
    ]
    if has_prev or has_next:
        nav_row: list[InlineKeyboardButton] = []
        if has_prev:
            nav_row.append(InlineKeyboardButton(text="⬅️ Предыдущая", callback_data=f"clients:page:{max(0, offset - limit)}"))
        if has_next:
            nav_row.append(InlineKeyboardButton(text="➡️ Следующая", callback_data=f"clients:page:{offset + limit}"))
        if nav_row:
            kb_rows.append(nav_row)
    await message.answer("Список клиентов:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.callback_query(F.data.regexp(r"^clients:page:\d+$"))
async def clients_page(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        offset = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректная страница", show_alert=True)
        return

    # Re-open the page with the same limit.
    await open_clients_menu(
        callback.message,
        limit=20,
        offset=offset,
        agent_tg_id=callback.from_user.id,
        filter_mode=_CLIENTS_FILTER_BY_AGENT.get(callback.from_user.id, "all"),
    )
    await callback.answer()


@router.callback_query(F.data.in_({"clients:filter:all", "clients:filter:invited"}))
async def clients_filter(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    mode = "invited" if callback.data.endswith("invited") else "all"
    _CLIENTS_FILTER_BY_AGENT[callback.from_user.id] = mode
    await open_clients_menu(
        callback.message,
        limit=20,
        offset=0,
        agent_tg_id=callback.from_user.id,
        filter_mode=mode,
    )
    await callback.answer()


def _kind_short(kind: str) -> tuple[str, str | None]:
    # Example: "Другой: <text>" -> ("Другой", "<text>")
    if kind.startswith("Другой:"):
        return "Другой", kind.split(":", 1)[1].strip() if ":" in kind else None
    return kind, None


async def _send_client_card(
    agent_tg_id: int,
    client_id: int,
    message: Message,
    *,
    exclude_client_document_ids: set[int] | None = None,
) -> None:
    c = await get_client(agent_tg_id, client_id)
    if c is None:
        await message.answer("Клиент не найден.", reply_markup=clients_menu_keyboard())
        return

    contracts = await list_contracts_for_client(agent_tg_id, client_id, limit=10)
    invited_user_ids = await list_invited_client_user_ids(agent_tg_id)
    documents = await list_client_documents(agent_tg_id, client_id, limit=5)
    if exclude_client_document_ids:
        documents = [d for d in documents if d.id not in exclude_client_document_ids]

    lines = [
        f"📇 Клиент #{c.id}",
        f"👤 {c.full_name}{' 🔗 по инвайту' if (getattr(c, 'source_user_id', None) in invited_user_ids) else ''}",
        f"📞 {c.phone or '—'}",
        f"📧 {c.email or '—'}",
        "",
        "📄 Договоры:",
    ]
    if not contracts:
        lines.append("— пока нет")
    else:
        has_docs_flags = []
        for ct in contracts:
            has_docs_flags.append(await contract_has_documents(agent_tg_id, ct.id))

        for ct, has_docs in zip(contracts, has_docs_flags):
            kind_short, _ = _kind_short(ct.contract_kind)
            mark = "📷 " if has_docs else ""
            lines.append(
                f"• #{ct.id} • {ct.contract_number} • {mark}{kind_short} • {ct.company} • "
                f"{ct.start_date:%d.%m.%Y} - {ct.end_date:%d.%m.%Y}"
            )

    if documents:
        lines.append("")
        lines.append("📎 Фото документов:")
        for d in documents[:5]:
            cap = f" — {d.caption}" if d.caption else ""
            lines.append(f"• #{d.id}{cap}")

    view_buttons: list[list[InlineKeyboardButton]] = []
    for ct in contracts[:3]:
        view_buttons.append(
            [InlineKeyboardButton(text=f"📄 Договор #{ct.contract_number}", callback_data=f"client:view_contract:{ct.id}")]
        )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить договор", callback_data=f"client:add_contract:{c.id}")],
            [InlineKeyboardButton(text="✏️ Редактировать клиента", callback_data=f"client:edit:{c.id}")],
            *(
                [[InlineKeyboardButton(text="🔗 Привязать по инвайту", callback_data=f"client:bind_invite:{c.id}")]]
                if getattr(c, "source_user_id", None) is None
                else []
            ),
            *view_buttons,
            [InlineKeyboardButton(text="📎 Добавить фото документов", callback_data=f"client:add_doc:{c.id}")],
            [InlineKeyboardButton(text="🗑 Удалить клиента", callback_data=f"client:delete:{c.id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="clients:back")],
        ]
    )

    if documents:
        doc_rows: list[list[InlineKeyboardButton]] = []
        for d in documents[:3]:
            doc_rows.append([InlineKeyboardButton(text=f"📷 Фото #{d.id}", callback_data=f"client:show_doc:{d.id}")])
        kb.inline_keyboard.extend(doc_rows)
    await message.answer("\n".join(lines), reply_markup=kb)


@router.message(StateFilter(None), F.text.regexp(r"^\d+$"))
async def choose_client_by_index(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    ids = _LAST_CLIENTS_BY_AGENT.get(message.from_user.id)
    if not ids:
        return
    idx = int((message.text or "").strip()) - 1
    if idx < 0 or idx >= len(ids):
        # Ignore unrelated numeric messages (e.g. contract numbers in other flows).
        return
    await _send_client_card(agent_tg_id=message.from_user.id, client_id=ids[idx], message=message)


@router.callback_query(F.data.startswith("client:bind_invite:"))
async def client_bind_invite_create(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        client_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    inv = await create_client_bind_invite(callback.from_user.id, client_id, ttl_hours=72)
    if inv is None:
        await callback.answer("Не удалось создать ссылку. Клиент уже привязан или не найден.", show_alert=True)
        return
    token = f"inv_{inv.token}"
    link = f"/start {token}"
    try:
        me = await callback.bot.get_me()
        if me.username:
            link = f"https://t.me/{me.username}?start={token}"
    except Exception:
        pass
    await callback.message.answer(
        "🔗 Уникальная ссылка привязки клиента (72 часа, 1 использование):\n"
        f"{link}\n\n"
        "Когда клиент откроет ссылку, эта карточка клиента будет привязана к его Telegram."
    )
    await callback.answer("Ссылка создана")


@router.message(F.text == ClientsMenu.BACK)
async def clients_back(message: Message, state: FSMContext) -> None:
    if await state.get_state() is not None:
        await state.clear()
    _LAST_CLIENTS_BY_AGENT.pop(message.from_user.id, None)
    _CLIENTS_FILTER_BY_AGENT.pop(message.from_user.id, None)
    await _set_reply_keyboard_silent_message(message, text="Выберите действие.", reply_markup=agent_menu())


class PaymentInsert(StatesGroup):
    search_contract = State()
    search_client = State()


@router.message(F.text == Btn.ADD_PAYMENT)
async def agent_add_payment_start(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    await _delete_user_button_message(message)
    await state.clear()
    await _flow_answer(
        message,
        "payment_insert",
        "Внести взнос. Как ищем?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Найти договор", callback_data="payins:search_contract")],
                [InlineKeyboardButton(text="Найти клиента", callback_data="payins:search_client")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="payins:back")],
            ]
        ),
    )


@router.callback_query(F.data == "payins:back")
async def agent_add_payment_back(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() is not None:
        await state.clear()
    await _clear_flow_prompt(callback.message, "payment_insert")
    await _set_reply_keyboard_silent_callback(callback, text="Выберите действие.", reply_markup=agent_menu())
    await callback.answer()


@router.callback_query(F.data == "payins:search_contract")
async def agent_add_payment_search_contract(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await state.clear()
    await state.set_state(PaymentInsert.search_contract)
    await _flow_answer(callback.message, "payment_insert", "Введите номер договора:", reply_markup=to_main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "payins:search_client")
async def agent_add_payment_search_client(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await state.clear()
    await state.set_state(PaymentInsert.search_client)
    await _flow_answer(
        callback.message,
        "payment_insert",
        "Введите текст для поиска клиента (имя/телефон/email):",
        reply_markup=to_main_menu_keyboard(),
    )
    await callback.answer()


@router.message(PaymentInsert.search_contract)
async def agent_add_payment_search_contract_query(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    query = (message.text or "").strip()
    if len(query) < 1:
        await _flow_answer(message, "payment_insert", "Введите номер договора.")
        return
    contracts = await search_contracts_by_number(message.from_user.id, query, limit=10)
    await state.clear()
    await _clear_flow_prompt(message, "payment_insert")
    if not contracts:
        await message.answer("По этому номеру договор не найден.", reply_markup=agent_menu())
        return

    kb_rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for ct in contracts:
        row.append(InlineKeyboardButton(text=f"#{ct.contract_number} (ID {ct.id})", callback_data=f"client:view_contract:{ct.id}"))
        if len(row) >= 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)

    await message.answer("Найденные договоры:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await _set_reply_keyboard_silent_message(message, text="Выберите действие.", reply_markup=agent_menu())


@router.message(PaymentInsert.search_client)
async def agent_add_payment_search_client_query(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    query = (message.text or "").strip()
    if len(query) < 2:
        await _flow_answer(message, "payment_insert", "Введите минимум 2 символа для поиска.")
        return
    items = await list_clients(message.from_user.id, query=query, limit=10)
    await state.clear()
    await _clear_flow_prompt(message, "payment_insert")
    if not items:
        await message.answer("По этому запросу клиенты не найдены.", reply_markup=agent_menu())
        return

    kb_rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for c in items:
        row.append(InlineKeyboardButton(text=f"{c.full_name} (ID {c.id})", callback_data=f"client:open:{c.id}"))
        if len(row) >= 1:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)

    await message.answer("Найденные клиенты:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await _set_reply_keyboard_silent_message(message, text="Выберите действие.", reply_markup=agent_menu())


class ClientAdd(StatesGroup):
    name = State()
    phone = State()
    email = State()


class ClientsSearch(StatesGroup):
    query = State()


class ClientEdit(StatesGroup):
    client_id = State()
    name = State()
    phone = State()
    email = State()


class ContractAdd(StatesGroup):
    # We store identifiers in FSM data (see state.update_data()).
    contract_number = State()
    company = State()
    contract_kind = State()
    contract_kind_other = State()
    currency = State()
    vehicle = State()
    signing_date = State()
    start_date = State()
    end_date = State()
    total_amount = State()
    insured_sum = State()
    payment_plan = State()
    initial_payment_amount = State()
    auto_plan_confirm = State()
    payments_count = State()
    payment_amount = State()
    payment_due_date = State()


class ClientDocAdd(StatesGroup):
    photo = State()


class ContractDocAdd(StatesGroup):
    photo = State()


@router.message(F.text == ClientsMenu.ADD)
async def start_add_client(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    await state.clear()
    await state.set_state(ClientAdd.name)
    await message.answer("Имя клиента?", reply_markup=to_main_menu_keyboard())


@router.message(F.text == ClientsMenu.SEARCH)
async def start_search_clients(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    await state.clear()
    await state.set_state(ClientsSearch.query)
    await message.answer("Введите текст для поиска (имя / телефон / email):", reply_markup=to_main_menu_keyboard())


@router.message(ClientsSearch.query)
async def clients_search_query(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Слишком короткий запрос. Введите минимум 2 символа.")
        return

    items = await list_clients(message.from_user.id, query=query, limit=10)
    await state.clear()

    if not items:
        await message.answer("Ничего не найдено по этому запросу.", reply_markup=clients_menu_keyboard())
        return

    lines = [f"🔎 Результаты по: {query}"]
    for c in items:
        phone = f" • {c.phone}" if c.phone else ""
        lines.append(f"#{c.id} • {c.full_name}{phone}")

    kb_rows: list[list[InlineKeyboardButton]] = []
    for c in items[:8]:
        kb_rows.append(
            [
                InlineKeyboardButton(text=f"Открыть #{c.id}", callback_data=f"client:open:{c.id}"),
                InlineKeyboardButton(text=f"✏️ #{c.id}", callback_data=f"client:edit:{c.id}"),
            ]
        )

    await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )
    await message.answer("Продолжить?", reply_markup=clients_menu_keyboard())


@router.message(F.text.casefold() == "отмена")
async def cancel_clients_flow(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        return
    # Avoid interfering with other FSM flows.
    allowed_prefixes = ("ClientAdd:", "ClientsSearch:", "ClientEdit:", "ContractAdd:")
    if not any(current.startswith(p) for p in allowed_prefixes):
        return
    await state.clear()
    await message.answer("Ок, отменил.", reply_markup=clients_menu_keyboard())


@router.message(ClientAdd.name)
async def add_client_step_name(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Слишком коротко. Введите имя клиента.")
        return
    await state.update_data(add_name=name)
    await state.set_state(ClientAdd.phone)
    await message.answer("Телефон (можно 'нет')?")


@router.message(ClientAdd.phone)
async def add_client_step_phone(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    phone = (message.text or "").strip()
    if phone.lower() == "нет":
        phone = ""
    await state.update_data(add_phone=phone or None)
    await state.set_state(ClientAdd.email)
    await message.answer("Email (можно 'нет')?")


@router.message(ClientAdd.email)
async def add_client_step_email(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    email = (message.text or "").strip()
    if email.lower() == "нет":
        email = ""
    data = await state.get_data()
    agent_id = message.from_user.id

    c = await create_client(
        agent_tg_id=agent_id,
        full_name=str(data["add_name"]),
        phone=data.get("add_phone"),
        email=email or None,
    )
    await state.clear()

    await message.answer(f"✅ Клиент сохранён: #{c.id} • {c.full_name}", reply_markup=clients_menu_keyboard())
    # Follow-up: show card.
    await message.answer(
        "Открыть карточку клиента?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Открыть", callback_data=f"client:open:{c.id}")]]
        ),
    )


@router.callback_query(F.data.startswith("client:open:"))
async def open_client_card(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        client_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    c = await get_client(callback.from_user.id, client_id)
    if c is None:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    contracts = await list_contracts_for_client(callback.from_user.id, client_id, limit=10)
    documents = await list_client_documents(callback.from_user.id, client_id, limit=5)

    def _kind_short(kind: str) -> tuple[str, str | None]:
        # Example: "Другой: <text>" -> ("Другой", "<text>")
        if kind.startswith("Другой:"):
            return "Другой", kind.split(":", 1)[1].strip() if ":" in kind else None
        return kind, None

    lines = [
        f"📇 Клиент #{c.id}",
        f"👤 {c.full_name}{' 🔗 по инвайту' if getattr(c, 'source_user_id', None) is not None else ''}",
        f"📞 {c.phone or '—'}",
        f"📧 {c.email or '—'}",
        "",
        "📄 Договоры:",
    ]
    if not contracts:
        lines.append("— пока нет")
    else:
        # Add a small marker if there are attached photos for this contract.
        has_docs_flags = []
        for ct in contracts:
            has_docs_flags.append(await contract_has_documents(callback.from_user.id, ct.id))

        for ct, has_docs in zip(contracts, has_docs_flags):
            kind_short, _ = _kind_short(ct.contract_kind)
            mark = "📷 " if has_docs else ""
            lines.append(
                f"• #{ct.id} • {ct.contract_number} • {mark}{kind_short} • {ct.company} • "
                f"{ct.start_date:%d.%m.%Y} - {ct.end_date:%d.%m.%Y}"
            )

    view_buttons: list[list[InlineKeyboardButton]] = []
    for ct in contracts[:3]:
        view_buttons.append([InlineKeyboardButton(text=f"📄 Договор #{ct.contract_number}", callback_data=f"client:view_contract:{ct.id}")])

    doc_buttons: list[list[InlineKeyboardButton]] = []
    for d in documents[:3]:
        doc_buttons.append([InlineKeyboardButton(text=f"📷 Фото #{d.id}", callback_data=f"client:show_doc:{d.id}")])

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить договор", callback_data=f"client:add_contract:{c.id}")],
            [InlineKeyboardButton(text="✏️ Редактировать клиента", callback_data=f"client:edit:{c.id}")],
            *(
                [[InlineKeyboardButton(text="🔗 Привязать по инвайту", callback_data=f"client:bind_invite:{c.id}")]]
                if getattr(c, "source_user_id", None) is None
                else []
            ),
            *view_buttons,
            [InlineKeyboardButton(text="📎 Добавить фото документов", callback_data=f"client:add_doc:{c.id}")],
            *doc_buttons,
            [InlineKeyboardButton(text="🗑 Удалить клиента", callback_data=f"client:delete:{c.id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="clients:back")],
        ]
    )
    await callback.message.answer("\n".join(lines), reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "clients:back")
async def clients_back_cb(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await callback.message.answer("Ок.", reply_markup=clients_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("client:edit:"))
async def start_edit_client(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        client_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    c = await get_client(callback.from_user.id, client_id)
    if c is None:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    await state.clear()
    await state.update_data(edit_client_id=client_id)
    await state.set_state(ClientEdit.name)
    await callback.message.answer(
        f"Новые имя клиента? Текущее: {c.full_name}\nМожно написать новое или 'оставить'.",
        reply_markup=to_main_menu_keyboard(),
    )
    await callback.answer()


@router.message(ClientEdit.name)
async def edit_client_name(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip()
    if txt.lower() == "оставить":
        await state.update_data(edit_name=None)
    else:
        await state.update_data(edit_name=txt)
    await state.set_state(ClientEdit.phone)
    await message.answer("Новый телефон? Можно 'оставить' или 'нет'.")


@router.message(ClientEdit.phone)
async def edit_client_phone(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip()
    if txt.lower() == "оставить":
        await state.update_data(edit_phone=None)
    elif txt.lower() == "нет":
        await state.update_data(edit_phone="")
    else:
        await state.update_data(edit_phone=txt)
    await state.set_state(ClientEdit.email)
    await message.answer("Новый email? Можно 'оставить' или 'нет'.")


@router.message(ClientEdit.email)
async def edit_client_email(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip()
    if txt.lower() == "оставить":
        await state.update_data(edit_email=None)
    elif txt.lower() == "нет":
        await state.update_data(edit_email="")
    else:
        await state.update_data(edit_email=txt)

    data = await state.get_data()
    client_id = int(data["edit_client_id"])
    c = await get_client(message.from_user.id, client_id)
    if c is None:
        await state.clear()
        await message.answer("Клиент не найден.", reply_markup=clients_menu_keyboard())
        return

    new_name = data.get("edit_name")
    new_phone = data.get("edit_phone")
    new_email = data.get("edit_email")

    updated = await update_client(
        agent_tg_id=message.from_user.id,
        client_id=client_id,
        full_name=str(new_name) if new_name is not None else c.full_name,
        # "оставить" => не меняем текущее значение (keep existing)
        # "нет" => делаем None (unset)
        phone=(c.phone if new_phone is None else (new_phone or None)),
        email=(c.email if new_email is None else (new_email or None)),
    )
    await state.clear()
    if updated is None:
        await message.answer("Не удалось обновить клиента.", reply_markup=clients_menu_keyboard())
        return
    await message.answer(f"✅ Клиент обновлён: #{updated.id} • {updated.full_name}", reply_markup=clients_menu_keyboard())


@router.callback_query(F.data.startswith("client:add_contract:"))
async def start_add_contract(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        client_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    await state.clear()
    await state.update_data(contract_add_client_id=client_id, contract_edit_id=None, payments=[])
    await state.set_state(ContractAdd.contract_number)
    await _flow_answer(
        callback.message,
        "contract_add",
        "Номер договора (любой формат, например: 1-2026 или 20262602):",
        reply_markup=to_main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("client:edit_contract:"))
async def start_edit_contract(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        contract_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    contract = await get_contract_detailed(callback.from_user.id, contract_id)
    if contract is None:
        await callback.answer("Договор не найден", show_alert=True)
        return

    # For simplicity, re-enter fields (no inline default editing).
    await state.clear()
    await state.update_data(
        contract_add_client_id=contract.client_id,
        contract_edit_id=contract_id,
        payments=[],
    )
    await state.set_state(ContractAdd.contract_number)
    await _flow_answer(
        callback.message,
        "contract_add",
        f"Номер договора? Текущее: {contract.contract_number}\nНапишите новое.",
        reply_markup=to_main_menu_keyboard(),
    )
    await callback.answer()


@router.message(ContractAdd.contract_number)
async def contract_step_number(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    raw = message.text or ""
    txt = raw.strip()
    if len(txt) < 1:
        await _flow_answer(message, "contract_add", "Введите номер договора.")
        return
    # Keep user input as-is (except newline trimming for transport).
    await state.update_data(contract_number=raw.strip("\r\n"))
    await state.set_state(ContractAdd.company)
    await _flow_answer(message, "contract_add", "Страховая компания по договору? (например: Белгосстрах)")


@router.message(ContractAdd.company)
async def contract_step_company(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip()
    if len(txt) < 2:
        await _flow_answer(message, "contract_add", "Введите компанию (минимум 2 символа).")
        return
    await state.update_data(contract_company=txt)
    await state.set_state(ContractAdd.contract_kind)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="КАСКО", callback_data="client:contract_kind:kasko"),
                InlineKeyboardButton(text="Имущество", callback_data="client:contract_kind:property"),
            ],
            [
                InlineKeyboardButton(text="Грузы", callback_data="client:contract_kind:cargo"),
                InlineKeyboardButton(text="Страховка за границу", callback_data="client:contract_kind:accident"),
            ],
            [
                InlineKeyboardButton(text="Ответственность экспедитора", callback_data="client:contract_kind:expeditor"),
                InlineKeyboardButton(text="CMR", callback_data="client:contract_kind:cmr"),
            ],
            [
                InlineKeyboardButton(text="ДМС", callback_data="client:contract_kind:dms"),
                InlineKeyboardButton(text="Другой вид", callback_data="client:contract_kind:other"),
            ],
        ]
    )
    await _flow_answer(message, "contract_add", "Выбери вид договора:", reply_markup=kb)


@router.callback_query(F.data.startswith("client:contract_kind:"))
async def contract_kind_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return

    key = callback.data.split(":", 2)[2]
    kind_map = {
        "kasko": "КАСКО",
        "property": "Имущество",
        "cargo": "Грузы",
        "accident": "Страховка за границу",
        "expeditor": "Ответственность экспедитора",
        "cmr": "CMR",
        "dms": "ДМС",
        "other": "Другой вид",
    }
    label = kind_map.get(key)
    if label is None:
        await callback.answer()
        return

    await state.update_data(contract_kind=label, contract_kind_key=key)
    if key == "other":
        await state.set_state(ContractAdd.contract_kind_other)
        await _flow_answer(callback.message, "contract_add", "Укажи конкретный вид договора (текстом).")
        await callback.answer()
        return

    await state.set_state(ContractAdd.currency)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="BYN", callback_data="client:contract_currency:BYN"),
                InlineKeyboardButton(text="USD", callback_data="client:contract_currency:USD"),
            ],
            [
                InlineKeyboardButton(text="EUR", callback_data="client:contract_currency:EUR"),
                InlineKeyboardButton(text="RUB", callback_data="client:contract_currency:RUB"),
            ],
            [
                InlineKeyboardButton(text="CNY", callback_data="client:contract_currency:CNY"),
            ],
        ]
    )
    await _flow_answer(callback.message, "contract_add", "Выбери валюту договорa:", reply_markup=kb)
    await callback.answer()


@router.message(ContractAdd.contract_kind_other)
async def contract_step_kind_other(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip()
    if len(txt) < 2:
        await _flow_answer(message, "contract_add", "Введите вид чуть подробнее.")
        return
    await state.update_data(contract_kind=f"Другой: {txt}")
    await state.set_state(ContractAdd.currency)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="BYN", callback_data="client:contract_currency:BYN"),
                InlineKeyboardButton(text="USD", callback_data="client:contract_currency:USD"),
            ],
            [
                InlineKeyboardButton(text="EUR", callback_data="client:contract_currency:EUR"),
                InlineKeyboardButton(text="RUB", callback_data="client:contract_currency:RUB"),
            ],
            [InlineKeyboardButton(text="CNY", callback_data="client:contract_currency:CNY")],
        ]
    )
    await _flow_answer(message, "contract_add", "Выбери валюту договора:", reply_markup=kb)


@router.callback_query(F.data.startswith("client:contract_currency:"))
async def contract_currency_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    currency = callback.data.split(":", 2)[2]
    await state.update_data(currency=currency)
    data = await state.get_data()
    kind_key = str(data.get("contract_kind_key") or "")
    if kind_key == "kasko":
        await state.set_state(ContractAdd.vehicle)
        await _flow_answer(
            callback.message,
            "contract_add",
            "Для КАСКО: какой автомобиль? (например: Toyota Camry 2020)",
            reply_markup=to_main_menu_keyboard(),
        )
    else:
        await state.set_state(ContractAdd.signing_date)
        await _flow_answer(
            callback.message,
            "contract_add",
            f"Дата заключения договора (дата оплаты первоначального взноса)? (например: 20.03.2026)\nВалюта: {currency}",
        )
    await callback.answer()


@router.message(ContractAdd.signing_date)
async def contract_step_signing_date(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    res = parse_date_ru(message.text or "", today=date.today())
    if res is None:
        await _flow_answer(message, "contract_add", "Не понял дату. Пример: 20.03.2026")
        return
    await state.update_data(signing_date_iso=res.target_date.isoformat())
    await state.set_state(ContractAdd.start_date)
    await _flow_answer(message, "contract_add", "Дата начала действия договора? (например: 20.03.2026)")


@router.message(ContractAdd.start_date)
async def contract_step_start_date(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    res = parse_date_ru(message.text or "", today=date.today())
    if res is None:
        await _flow_answer(message, "contract_add", "Не понял дату. Пример: 20.03.2026")
        return
    await state.update_data(start_date_iso=res.target_date.isoformat())
    data = await state.get_data()
    kind_key = str(data.get("contract_kind_key") or "")
    if kind_key == "accident":
        # For travel insurance duration is flexible; ask explicit end date.
        await state.set_state(ContractAdd.end_date)
        await _flow_answer(message, "contract_add", "Дата окончания договора? (например: 20.03.2026)")
        return

    # For all other types end date is automatically start + 1 year.
    end_date = _add_months(res.target_date, 12) - timedelta(days=1)
    await state.update_data(end_date_iso=end_date.isoformat())
    await state.set_state(ContractAdd.insured_sum)
    currency = str(data.get("currency") or "BYN")
    await _flow_answer(
        message,
        "contract_add",
        f"Дата окончания рассчитана автоматически: {end_date:%d.%m.%Y}\n"
        f"Страховая сумма / лимит ответственности ({currency}), число. Например: 20000\n"
        f"(Страховая сумма — лимит покрытия, страховой взнос — стоимость полиса.)"
    )


@router.message(ContractAdd.vehicle)
async def contract_step_vehicle(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip()
    if len(txt) < 3:
        await _flow_answer(message, "contract_add", "Введите автомобиль чуть подробнее (минимум 3 символа).")
        return
    await state.update_data(contract_vehicle=txt)
    data = await state.get_data()
    currency = str(data.get("currency") or "BYN")
    await state.set_state(ContractAdd.signing_date)
    await _flow_answer(
        message,
        "contract_add",
        f"Дата заключения договора (дата оплаты первоначального взноса)? (например: 20.03.2026)\nВалюта: {currency}",
    )


@router.message(ContractAdd.end_date)
async def contract_step_end_date(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    res = parse_date_ru(message.text or "", today=date.today())
    if res is None:
        await _flow_answer(message, "contract_add", "Не понял дату. Пример: 20.03.2026")
        return
    await state.update_data(end_date_iso=res.target_date.isoformat())
    await state.set_state(ContractAdd.insured_sum)
    data = await state.get_data()
    currency = str(data.get("currency") or "BYN")
    await _flow_answer(
        message,
        "contract_add",
        f"Страховая сумма / лимит ответственности ({currency}), число. Например: 20000\n"
        f"(Страховая сумма — лимит покрытия, страховой взнос — стоимость полиса.)",
    )


def _add_months(src: date, months: int) -> date:
    y = src.year + (src.month - 1 + months) // 12
    m = (src.month - 1 + months) % 12 + 1
    d = min(src.day, calendar.monthrange(y, m)[1])
    return date(y, m, d)


def _build_auto_schedule(annual_total_minor: int, initial_minor: int, start_date: date, stages: int) -> list[dict[str, str | int]]:
    """
    Build pending schedule (excluding first paid installment).
    stages: total number of payment stages in year (1/2/4/12).
    """
    if stages <= 1:
        return []

    remainder = max(0, annual_total_minor - initial_minor)
    n = stages - 1
    if n <= 0 or remainder <= 0:
        return []

    base = remainder // n
    rem = remainder % n
    interval_months = max(1, 12 // stages)
    rows: list[dict[str, str | int]] = []
    for i in range(1, n + 1):
        amount_minor = base + (1 if i <= rem else 0)
        # End of each period is one day before next period boundary.
        due = _add_months(start_date, interval_months * i) - timedelta(days=1)
        rows.append({"amount_minor": amount_minor, "due_iso": due.isoformat()})
    return rows


async def _persist_contract_from_state(agent_id: int, state: FSMContext, message: Message) -> tuple[object | None, int]:
    data = await state.get_data()
    client_id = int(data["contract_add_client_id"])
    contract_id = data.get("contract_edit_id")
    contract_number = str(data["contract_number"])
    company = str(data["contract_company"])
    contract_kind = str(data["contract_kind"])
    vehicle_description = data.get("contract_vehicle")
    start_date_iso = str(data["start_date_iso"])
    end_date_iso = str(data["end_date_iso"])
    total_amount_minor = int(data["total_amount_minor"])
    insured_sum_minor = int(data.get("insured_sum_minor") or 0) or None
    currency = str(data.get("currency") or "BYN")
    initial_payment_minor = int(data["initial_payment_minor"])
    signing_date_iso = str(data["signing_date_iso"])

    payments = list(data.get("payments") or [])
    payments_typed: list[tuple[int, date]] = [
        (int(p["amount_minor"]), date.fromisoformat(str(p["due_iso"]))) for p in payments
    ]

    if contract_id is None:
        ct = await create_contract_for_client(
            agent_tg_id=agent_id,
            client_id=client_id,
            contract_number=contract_number,
            company=company,
            contract_kind=contract_kind,
            start_date=date.fromisoformat(start_date_iso),
            end_date=date.fromisoformat(end_date_iso),
            total_amount_minor=total_amount_minor,
            insured_sum_minor=insured_sum_minor,
            currency=currency,
            initial_payment_amount_minor=initial_payment_minor,
            initial_payment_due_date=date.fromisoformat(signing_date_iso),
            vehicle_description=vehicle_description,
            payments=payments_typed,
        )
    else:
        ct = await update_contract_for_client(
            agent_tg_id=agent_id,
            contract_id=int(contract_id),
            contract_number=contract_number,
            company=company,
            contract_kind=contract_kind,
            start_date=date.fromisoformat(start_date_iso),
            end_date=date.fromisoformat(end_date_iso),
            total_amount_minor=total_amount_minor,
            insured_sum_minor=insured_sum_minor,
            currency=currency,
            initial_payment_amount_minor=initial_payment_minor,
            initial_payment_due_date=date.fromisoformat(signing_date_iso),
            vehicle_description=vehicle_description,
            payments=payments_typed,
        )

    await state.clear()
    if ct is None:
        await message.answer("Не удалось сохранить договор. Проверьте данные.", reply_markup=clients_menu_keyboard())
        return None, client_id

    await message.answer(f"✅ Договор сохранён: #{ct.id} • {ct.contract_number}", reply_markup=clients_menu_keyboard())
    await message.answer(
        "Что дальше?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📎 Добавить фото договора", callback_data=f"contract:add_doc:{ct.id}")],
                [InlineKeyboardButton(text="👀 Открыть договор", callback_data=f"client:view_contract:{ct.id}")],
                [InlineKeyboardButton(text="⬅️ К карточке клиента", callback_data=f"client:open:{client_id}")],
            ]
        ),
    )
    return ct, client_id


@router.message(ContractAdd.total_amount)
async def contract_step_total_amount(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        val = float(txt)
    except ValueError:
        await _flow_answer(message, "contract_add", "Сумма должна быть числом. Например: 1500.50")
        return
    if val <= 0:
        await _flow_answer(message, "contract_add", "Сумма должна быть больше 0.")
        return
    data = await state.get_data()
    currency = str(data.get("currency") or "BYN")
    await state.update_data(total_amount_minor=int(round(val * 100)), currency=currency)
    await state.set_state(ContractAdd.payment_plan)
    await message.answer(
        "Выбери график оплаты:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Единовременно", callback_data="client:plan:1")],
                [InlineKeyboardButton(text="В 2 этапа", callback_data="client:plan:2")],
                [InlineKeyboardButton(text="В 4 этапа", callback_data="client:plan:4")],
                [InlineKeyboardButton(text="В 12 этапов", callback_data="client:plan:12")],
            ]
        ),
    )


@router.message(ContractAdd.insured_sum)
async def contract_step_insured_sum(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        val = float(txt)
    except ValueError:
        await message.answer("Страховая сумма (лимит ответственности) должна быть числом. Например: 20000")
        return
    if val <= 0:
        await message.answer("Страховая сумма (лимит ответственности) должна быть больше 0.")
        return
    await state.update_data(insured_sum_minor=int(round(val * 100)))
    data = await state.get_data()
    currency = str(data.get("currency") or "BYN")
    await state.set_state(ContractAdd.total_amount)
    await _flow_answer(
        message,
        "contract_add",
        f"Страховой взнос (годовой, {currency}), число. Например: 1500.50\n"
        f"(Это стоимость полиса, которую клиент оплачивает по графику.)",
    )


@router.callback_query(ContractAdd.payment_plan, F.data.startswith("client:plan:"))
async def contract_step_payment_plan(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        stages = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный график", show_alert=True)
        return
    if stages not in (1, 2, 4, 12):
        await callback.answer("Некорректный график", show_alert=True)
        return

    await state.update_data(plan_stages=stages)
    data = await state.get_data()
    total_amount_minor = int(data["total_amount_minor"])
    currency = str(data.get("currency") or "BYN")
    if stages == 1:
        # One-time: initial paid installment equals annual total.
        await state.update_data(
            initial_payment_minor=total_amount_minor,
            payments=[],
            payments_expected=0,
            payment_idx=0,
        )
        await state.set_state(ContractAdd.auto_plan_confirm)
        signing_date_iso = str(data["signing_date_iso"])
        await callback.message.answer(
            "Проверь график перед сохранением:\n"
            f"• 1-й взнос (оплачен): {date.fromisoformat(signing_date_iso):%d.%m.%Y} — {total_amount_minor/100:.2f} {currency}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Сохранить", callback_data="client:plan:confirm_save")],
                ]
            ),
        )
        await callback.answer()
        return

    await state.set_state(ContractAdd.initial_payment_amount)
    await callback.message.answer(f"Первоначальный взнос ({currency}), число. Например: 150.50")
    await callback.answer()


@router.message(ContractAdd.initial_payment_amount)
async def contract_step_initial_payment_amount(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        val = float(txt)
    except ValueError:
        await message.answer("Первоначальный взнос должен быть числом. Например: 150.50")
        return
    if val <= 0:
        await message.answer("Первоначальный взнос должен быть > 0.")
        return

    data = await state.get_data()
    currency = str(data.get("currency") or "BYN")
    initial_payment_minor = int(round(val * 100))
    total_amount_minor = int(data["total_amount_minor"])
    if initial_payment_minor > total_amount_minor:
        await message.answer("Первоначальный взнос не может быть больше годового взноса.")
        return

    stages = int(data["plan_stages"])
    start_date_iso = str(data["start_date_iso"])
    start_date_val = date.fromisoformat(start_date_iso)
    auto_payments = _build_auto_schedule(
        annual_total_minor=total_amount_minor,
        initial_minor=initial_payment_minor,
        start_date=start_date_val,
        stages=stages,
    )

    await state.update_data(
        initial_payment_minor=initial_payment_minor,
        payments=auto_payments,
        payments_expected=len(auto_payments),
        payment_idx=0,
        currency=currency,
    )
    await state.set_state(ContractAdd.auto_plan_confirm)

    signing_date_iso = str(data["signing_date_iso"])
    remainder_minor = max(0, total_amount_minor - initial_payment_minor)
    lines = [
        "Проверь график перед сохранением:",
        f"• Годовой взнос: {total_amount_minor/100:.2f} {currency}",
        f"• Первоначальный: {initial_payment_minor/100:.2f} {currency}",
        f"• Остаток: {remainder_minor/100:.2f} {currency}",
        f"• Этапов оплаты: {stages}",
        "",
        f"• 1-й взнос (оплачен): {date.fromisoformat(signing_date_iso):%d.%m.%Y} — {initial_payment_minor/100:.2f} {currency}",
    ]
    for idx, p in enumerate(auto_payments, start=2):
        lines.append(f"• {idx}-й взнос: {date.fromisoformat(str(p['due_iso'])):%d.%m.%Y} — {int(p['amount_minor'])/100:.2f} {currency}")
    if not auto_payments:
        lines.append("• Дальнейших платежей нет (остаток 0.00).")

    await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Сохранить", callback_data="client:plan:confirm_save")],
                [InlineKeyboardButton(text="✏️ Изменить этапы вручную", callback_data="client:plan:manual_edit")],
            ]
        ),
    )


@router.callback_query(ContractAdd.auto_plan_confirm, F.data == "client:plan:confirm_save")
async def contract_auto_plan_confirm_save(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await _persist_contract_from_state(callback.from_user.id, state, callback.message)
    await callback.answer()


@router.callback_query(ContractAdd.auto_plan_confirm, F.data == "client:plan:manual_edit")
async def contract_auto_plan_manual_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    data = await state.get_data()
    auto_payments = list(data.get("payments") or [])
    n = len(auto_payments)
    if n <= 0:
        await callback.message.answer("Нет этапов для ручного редактирования. Можно сохранить текущий график.")
        await callback.answer()
        return
    await state.update_data(payments_expected=n, payment_idx=0, payments=[])
    await state.set_state(ContractAdd.payment_amount)
    currency = str(data.get("currency") or "BYN")
    await callback.message.answer(f"Платёж 2: сумма ({currency}), число.")
    await callback.answer()


@router.message(ContractAdd.payments_count)
async def contract_step_payments_count(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    raw = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        # Allow: 3, 3.0, 3,0
        n_float = float(raw)
        n = int(n_float)
    except ValueError:
        await message.answer("Введите число платежей, например: 3")
        return
    if abs(n_float - n) > 1e-9:
        await message.answer("Количество платежей должно быть целым числом, например: 3")
        return
    if n < 1 or n > 24:
        await message.answer("Количество платежей должно быть от 1 до 24.")
        return
    await state.update_data(payments_expected=n, payment_idx=0, payments=[])
    await state.set_state(ContractAdd.payment_amount)
    data = await state.get_data()
    currency = str(data.get("currency") or "BYN")
    await message.answer(f"Платёж 2: сумма ({currency}), число.")


@router.message(ContractAdd.payment_amount)
async def contract_step_payment_amount(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    data = await state.get_data()
    idx = int(data["payment_idx"])
    try:
        val = float((message.text or "").strip().replace(" ", "").replace(",", "."))
    except ValueError:
        await message.answer("Введите сумму числом, например: 250")
        return
    if val <= 0:
        await message.answer("Сумма должна быть > 0.")
        return
    await state.update_data(payment_amount_minor=int(round(val * 100)))
    await state.set_state(ContractAdd.payment_due_date)
    data = await state.get_data()
    currency = str(data.get("currency") or "BYN")
    await message.answer(f"Платёж {idx + 2}: дата платежа (например: 20.03.2026)\nВалюта: {currency}")


@router.message(ContractAdd.payment_due_date)
async def contract_step_payment_due_date(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    data = await state.get_data()
    idx = int(data["payment_idx"])
    res = parse_date_ru(message.text or "", today=date.today())
    if res is None:
        await message.answer("Не понял дату платежа. Пример: 20.03.2026")
        return
    # Append to schedule.
    payments = list(data.get("payments") or [])
    # Store only primitives in FSM context.
    payments.append({"amount_minor": int(data["payment_amount_minor"]), "due_iso": res.target_date.isoformat()})
    n_expected = int(data["payments_expected"])
    idx += 1

    await state.update_data(payments=payments, payment_idx=idx)
    if idx >= n_expected:
        await _persist_contract_from_state(message.from_user.id, state, message)
        return

    await state.set_state(ContractAdd.payment_amount)
    currency_next = str(data.get("currency") or "BYN")
    await message.answer(f"Платёж {idx + 2}: сумма ({currency_next}), число.")


async def _build_contract_view_text(
    agent_tg_id: int,
    contract_id: int,
    *,
    exclude_contract_document_ids: set[int] | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    ct = await get_contract_detailed(agent_tg_id, contract_id)
    if ct is None:
        return "Договор не найден", InlineKeyboardMarkup(inline_keyboard=[])

    def _kind_short(kind: str) -> tuple[str, str | None]:
        if kind.startswith("Другой:"):
            return "Другой", kind.split(":", 1)[1].strip() if ":" in kind else None
        return kind, None

    payments_lines: list[str] = []
    for p in ct.payments:
        status = "оплачено" if p.status == PaymentStatus.paid else "ожидает оплаты"
        payments_lines.append(
            f"• {p.due_date:%d.%m.%Y}: {p.amount_minor/100:.2f} {ct.currency} — {status}"
        )

    contract_docs = await list_contract_documents(agent_tg_id, ct.id, limit=5)
    if exclude_contract_document_ids:
        contract_docs = [d for d in contract_docs if d.id not in exclude_contract_document_ids]

    terminate_rows = (
        [[InlineKeyboardButton(text="🛑 Прекратить договор", callback_data=f"contract:terminate:{ct.id}")]]
        if ct.status == ContractStatus.active
        else []
    )

    pending_exists = ct.status == ContractStatus.active and any(p.status == PaymentStatus.pending for p in ct.payments)
    mark_rows = (
        [[InlineKeyboardButton(text="💳 Отметить взнос", callback_data=f"contract:mark_payment_start:{ct.id}")]]
        if pending_exists
        else []
    )
    notify_rows = (
        [[InlineKeyboardButton(text="📨 Напомнить клиенту", callback_data=f"contract:notify_client:{ct.id}")]]
        if pending_exists
        else []
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Редактировать договор", callback_data=f"client:edit_contract:{ct.id}")],
            [InlineKeyboardButton(text="⬅️ Назад к клиенту", callback_data=f"client:open:{ct.client_id}")],
            [InlineKeyboardButton(text="📎 Добавить фото договора", callback_data=f"contract:add_doc:{ct.id}")],
            *terminate_rows,
            *mark_rows,
            *notify_rows,
            [InlineKeyboardButton(text="🗑 Удалить договор", callback_data=f"contract:delete:{ct.id}")],
        ]
    )

    text = (
        f"📄 Договор #{ct.id}\n"
        f"Номер: {ct.contract_number}\n"
        f"Компания: {ct.company}\n"
        f"Вид: {_kind_short(ct.contract_kind)[0]}\n"
        + (f"Авто: {ct.vehicle_description}\n" if ct.vehicle_description else "")
        + f"Даты: {ct.start_date:%d.%m.%Y} - {ct.end_date:%d.%m.%Y}\n"
        + f"Статус: {'действует' if ct.status == ContractStatus.active else 'прекращен'}\n"
        + f"Страховой взнос (годовой): {ct.total_amount_minor/100:.2f} {ct.currency}\n"
        + (
            f"Страховая сумма: {ct.insured_sum_minor/100:.2f} {ct.currency}\n\n"
            if ct.insured_sum_minor is not None
            else "\n"
        )
        + f"График платежей:\n"
        + ("\n".join(payments_lines) if payments_lines else "— нет платежей")
    )

    # If user entered "Другой: <subtype>", show subtype line.
    kind_short, kind_detail = _kind_short(ct.contract_kind)
    if kind_detail:
        text = text.replace(f"Вид: {kind_short}\n", f"Вид: {kind_short} ({kind_detail})\n")

    if contract_docs:
        text = text + "\n📎 Фото договора:\n" + "\n".join(
            f"• #{d.id}{' — ' + d.caption if d.caption else ''}" for d in contract_docs[:5]
        )
        for d in contract_docs[:3]:
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"📷 Фото #{d.id}", callback_data=f"contract:show_doc:{d.id}")])

    return text, kb


@router.callback_query(F.data.startswith("client:view_contract:"))
async def view_contract(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        contract_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    ct = await get_contract_detailed(callback.from_user.id, contract_id)
    if ct is None:
        await callback.answer("Договор не найден", show_alert=True)
        return

    def _kind_short(kind: str) -> tuple[str, str | None]:
        if kind.startswith("Другой:"):
            return "Другой", kind.split(":", 1)[1].strip() if ":" in kind else None
        return kind, None

    payments_lines: list[str] = []
    for p in ct.payments:
        status = "оплачено" if p.status == PaymentStatus.paid else "ожидает оплаты"
        payments_lines.append(
            f"• {p.due_date:%d.%m.%Y}: {p.amount_minor/100:.2f} {ct.currency} — {status}"
        )

    contract_docs = await list_contract_documents(callback.from_user.id, ct.id, limit=5)

    terminate_rows = (
        [[InlineKeyboardButton(text="🛑 Прекратить договор", callback_data=f"contract:terminate:{ct.id}")]]
        if ct.status == ContractStatus.active
        else []
    )

    pending_exists = ct.status == ContractStatus.active and any(p.status == PaymentStatus.pending for p in ct.payments)
    mark_rows = (
        [[InlineKeyboardButton(text="💳 Отметить взнос", callback_data=f"contract:mark_payment_start:{ct.id}")]]
        if pending_exists
        else []
    )
    notify_rows = (
        [[InlineKeyboardButton(text="📨 Напомнить клиенту", callback_data=f"contract:notify_client:{ct.id}")]]
        if pending_exists
        else []
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Редактировать договор", callback_data=f"client:edit_contract:{ct.id}")],
            [InlineKeyboardButton(text="⬅️ Назад к клиенту", callback_data=f"client:open:{ct.client_id}")],
            [InlineKeyboardButton(text="📎 Добавить фото договора", callback_data=f"contract:add_doc:{ct.id}")],
            *terminate_rows,
            *mark_rows,
            *notify_rows,
            [InlineKeyboardButton(text="🗑 Удалить договор", callback_data=f"contract:delete:{ct.id}")],
        ]
    )

    text = (
        f"📄 Договор #{ct.id}\n"
        f"Номер: {ct.contract_number}\n"
        f"Компания: {ct.company}\n"
        f"Вид: {_kind_short(ct.contract_kind)[0]}\n"
        + (f"Авто: {ct.vehicle_description}\n" if ct.vehicle_description else "")
        + f"Даты: {ct.start_date:%d.%m.%Y} - {ct.end_date:%d.%m.%Y}\n"
        + f"Статус: {'действует' if ct.status == ContractStatus.active else 'прекращен'}\n"
        + f"Страховой взнос (годовой): {ct.total_amount_minor/100:.2f} {ct.currency}\n"
        + (
            f"Страховая сумма: {ct.insured_sum_minor/100:.2f} {ct.currency}\n\n"
            if ct.insured_sum_minor is not None
            else "\n"
        )
        + f"График платежей:\n"
        + ("\n".join(payments_lines) if payments_lines else "— нет платежей")
    )
    # If user entered "Другой: <subtype>", show subtype line.
    kind_short, kind_detail = _kind_short(ct.contract_kind)
    if kind_detail:
        text = text.replace(f"Вид: {kind_short}\n", f"Вид: {kind_short} ({kind_detail})\n")

    if contract_docs:
        text = text + "\n📎 Фото договора:\n" + "\n".join(
            f"• #{d.id}{' — ' + d.caption if d.caption else ''}" for d in contract_docs[:5]
        )

        # Add photo buttons to the keyboard as well.
        doc_rows: list[list[InlineKeyboardButton]] = []
        for d in contract_docs[:3]:
            doc_rows.append([InlineKeyboardButton(text=f"📷 Фото #{d.id}", callback_data=f"contract:show_doc:{d.id}")])
        kb.inline_keyboard.extend(doc_rows)

    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("contract:notify_client:"))
async def notify_client_from_contract(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        contract_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    ct = await get_contract_detailed(callback.from_user.id, contract_id)
    if ct is None:
        await callback.answer("Договор не найден", show_alert=True)
        return
    tgt = await get_contract_bound_client_tg(callback.from_user.id, contract_id)
    if tgt is None:
        await callback.answer("Клиент не привязан к Telegram", show_alert=True)
        return
    client_tg_id, _client_name = tgt
    pending = [p for p in ct.payments if p.status == PaymentStatus.pending]
    if not pending:
        await callback.answer("Нет ожидающих взносов", show_alert=True)
        return
    pending_sorted = sorted(pending, key=lambda p: p.due_date)
    lines = [
        "⏰ Напоминание по договору",
        f"Номер: {ct.contract_number}",
        f"Компания: {ct.company}",
        "",
        "Ближайшие взносы:",
    ]
    for p in pending_sorted[:5]:
        lines.append(f"• {p.due_date:%d.%m.%Y}: {p.amount_minor/100:.2f} {ct.currency}")
    if len(pending_sorted) > 5:
        lines.append(f"… и ещё {len(pending_sorted) - 5} взносов")
    try:
        await callback.bot.send_message(client_tg_id, "\n".join(lines))
    except Exception:
        await callback.answer("Не удалось отправить сообщение клиенту", show_alert=True)
        return
    await callback.answer("Уведомление отправлено")


@router.callback_query(F.data.startswith("client:add_doc:"))
async def start_add_client_doc(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        client_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    await state.clear()
    await state.update_data(doc_client_id=client_id)
    await state.set_state(ClientDocAdd.photo)
    await callback.message.answer(
        "Отправь фото документов для клиента. "
        "Можно добавить подпись к фото (не обязательно).",
        reply_markup=to_main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("contract:add_doc:"))
async def start_add_contract_doc(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        contract_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    await state.clear()
    await state.update_data(doc_contract_id=contract_id)
    await state.set_state(ContractDocAdd.photo)
    await callback.message.answer(
        "Отправь фото документов к договору. "
        "Можно добавить подпись к фото (не обязательно).",
        reply_markup=to_main_menu_keyboard(),
    )
    await callback.answer()


@router.message(ClientDocAdd.photo, F.photo)
async def add_client_doc_photo(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    data = await state.get_data()
    client_id = int(data["doc_client_id"])

    # Take the best quality (usually the last one).
    p = message.photo[-1]
    file_id = p.file_id
    file_unique_id = getattr(p, "file_unique_id", None)
    caption = message.caption.strip() if message.caption else None

    doc = await create_client_document(
        agent_tg_id=message.from_user.id,
        client_id=client_id,
        file_id=file_id,
        file_unique_id=file_unique_id,
        caption=caption,
    )
    await state.clear()
    if doc is None:
        await message.answer("Не удалось сохранить документ. Проверьте, что клиент принадлежит вам.")
        return

    await message.answer(f"✅ Фото сохранено: #{doc.id}", reply_markup=clients_menu_keyboard())
    await message.answer(
        "Открыть карточку клиента снова?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Открыть", callback_data=f"client:open:{client_id}")]]
        ),
    )


@router.message(ClientDocAdd.photo)
async def add_client_doc_photo_wrong(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    await message.answer("Пришли, пожалуйста, именно фото документов.")


@router.message(ContractDocAdd.photo, F.photo)
async def add_contract_doc_photo(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    data = await state.get_data()
    contract_id = int(data["doc_contract_id"])

    p = message.photo[-1]
    file_id = p.file_id
    file_unique_id = getattr(p, "file_unique_id", None)
    caption = message.caption.strip() if message.caption else None

    doc = await create_contract_document(
        agent_tg_id=message.from_user.id,
        contract_id=contract_id,
        file_id=file_id,
        file_unique_id=file_unique_id,
        caption=caption,
    )
    await state.clear()
    if doc is None:
        await message.answer("Не удалось сохранить документ. Проверьте, что договор принадлежит вам.")
        return

    await message.answer(f"✅ Фото сохранено: #{doc.id}")
    await message.answer(
        "Открыть карточку договора снова?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Открыть", callback_data=f"client:view_contract:{contract_id}")]]
        ),
    )


@router.message(ContractDocAdd.photo)
async def add_contract_doc_photo_wrong(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    await message.answer("Пришли, пожалуйста, именно фото документов.")


@router.callback_query(F.data.startswith("contract:show_doc:"))
async def show_contract_doc(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        doc_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    doc = await get_contract_document(callback.from_user.id, doc_id)
    if doc is None:
        await callback.answer("Документ не найден", show_alert=True)
        return

    # Remember contract view message_id to refresh UI after deletion.
    if callback.message is not None:
        _CONTRACT_DOC_CARD_MSG[(callback.from_user.id, doc.id)] = (doc.contract_id, callback.message.message_id)

    await callback.bot.send_photo(
        chat_id=callback.from_user.id,
        photo=doc.file_id,
        caption=doc.caption,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Удалить фото договора", callback_data=f"contract:delete_doc:{doc.id}")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("client:show_doc:"))
async def show_client_doc(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        doc_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    doc = await get_client_document(callback.from_user.id, doc_id)
    if doc is None:
        await callback.answer("Документ не найден", show_alert=True)
        return

    # Remember client card message_id to refresh UI after deletion.
    if callback.message is not None:
        _CLIENT_DOC_CARD_MSG[(callback.from_user.id, doc.id)] = (doc.client_id, callback.message.message_id)

    # Re-send photo using stored file_id.
    await callback.bot.send_photo(
        chat_id=callback.from_user.id,
        photo=doc.file_id,
        caption=doc.caption,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Удалить фото документа", callback_data=f"client:delete_doc:{doc.id}")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^client:delete_doc:\d+$"))
async def delete_client_doc_start(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        doc_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    photo_message_id = callback.message.message_id if callback.message is not None else None
    await callback.message.answer(
        "Удалить фото документа безвозвратно?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Да, удалить",
                        callback_data=f"client:delete_doc_confirm:{doc_id}:{photo_message_id}",
                    )
                ],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="client:delete_doc_cancel")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "client:delete_doc_cancel")
async def delete_client_doc_cancel(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.regexp(r"^client:delete_doc_confirm:\d+:\d+$"))
async def delete_client_doc_confirm(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        parts = callback.data.split(":", 3)
        doc_id = int(parts[2])
        photo_message_id = int(parts[3])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    doc = await get_client_document(callback.from_user.id, doc_id)
    client_id = doc.client_id if doc is not None else None

    ok = await delete_client_document(callback.from_user.id, doc_id)
    if ok:
        # Also remove the photo message from chat to make the UX immediate.
        try:
            await callback.bot.delete_message(chat_id=callback.from_user.id, message_id=photo_message_id)
        except Exception:
            # Fallback: at least hide inline keyboard.
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        refreshed_from_cache = False
        # Refresh stale card message where the photo was listed (best-effort).
        key = (callback.from_user.id, doc_id)
        if key in _CLIENT_DOC_CARD_MSG:
            client_id_cached, card_message_id = _CLIENT_DOC_CARD_MSG.pop(key)
            try:
                await callback.bot.delete_message(chat_id=callback.from_user.id, message_id=card_message_id)
            except Exception:
                pass
            # If we could remove the old card, re-send the updated one.
            if client_id_cached is not None:
                await _send_client_card(
                    callback.from_user.id,
                    client_id_cached,
                    callback.message,
                    exclude_client_document_ids={doc_id},
                )
                refreshed_from_cache = True

        # Even if message_id couldn't be found/deleted, show updated card.
        if client_id is not None and not refreshed_from_cache:
            await _send_client_card(
                callback.from_user.id,
                client_id,
                callback.message,
                exclude_client_document_ids={doc_id},
            )

    await callback.message.answer(
        "✅ Фото документа удалено." if ok else "Не удалось удалить (проверьте доступ)."
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^contract:delete_doc:\d+$"))
async def delete_contract_doc_start(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        doc_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    photo_message_id = callback.message.message_id if callback.message is not None else None
    await callback.message.answer(
        "Удалить фото договора безвозвратно?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Да, удалить",
                        callback_data=f"contract:delete_doc_confirm:{doc_id}:{photo_message_id}",
                    )
                ],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="contract:delete_doc_cancel")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "contract:delete_doc_cancel")
async def delete_contract_doc_cancel(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.regexp(r"^contract:delete_doc_confirm:\d+:\d+$"))
async def delete_contract_doc_confirm(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        parts = callback.data.split(":", 3)
        doc_id = int(parts[2])
        photo_message_id = int(parts[3])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    doc = await get_contract_document(callback.from_user.id, doc_id)
    contract_id = doc.contract_id if doc is not None else None

    ok = await delete_contract_document(callback.from_user.id, doc_id)
    if ok:
        try:
            await callback.bot.delete_message(chat_id=callback.from_user.id, message_id=photo_message_id)
        except Exception:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        sent_updated = False
        # Refresh stale contract view where the photo was listed (best-effort).
        key = (callback.from_user.id, doc_id)
        if key in _CONTRACT_DOC_CARD_MSG:
            cached_contract_id, card_message_id = _CONTRACT_DOC_CARD_MSG.pop(key)
            try:
                await callback.bot.delete_message(chat_id=callback.from_user.id, message_id=card_message_id)
            except Exception:
                pass
            if cached_contract_id is not None:
                text, kb = await _build_contract_view_text(
                    callback.from_user.id,
                    cached_contract_id,
                    exclude_contract_document_ids={doc_id},
                )
                await callback.message.answer(text, reply_markup=kb)
                sent_updated = True

        # Even if we couldn't delete old message, re-send updated view.
        if not sent_updated and contract_id is not None:
            text, kb = await _build_contract_view_text(
                callback.from_user.id,
                contract_id,
                exclude_contract_document_ids={doc_id},
            )
            await callback.message.answer(text, reply_markup=kb)
    await callback.message.answer(
        "✅ Фото договора удалено." if ok else "Не удалось удалить (проверьте доступ)."
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^contract:delete:\d+$"))
async def delete_contract_start(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        contract_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    await callback.message.answer(
        "Удалить договор безвозвратно?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"contract:delete_confirm:{contract_id}")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="contract:delete_cancel")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "contract:delete_cancel")
async def delete_contract_cancel(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.regexp(r"^contract:delete_confirm:\d+$"))
async def delete_contract_confirm(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        contract_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    ok = await delete_contract(callback.from_user.id, contract_id)
    await callback.message.answer("✅ Договор удалён." if ok else "Не удалось удалить (проверьте доступ).")
    await callback.answer()


@router.callback_query(F.data.regexp(r"^contract:terminate:\d+$"))
async def terminate_contract_start(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        contract_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    await callback.message.answer(
        "Прекратить договор? (дальнейшие шаги оплаты/комиссий будут учитывать прекращение)",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, прекратить", callback_data=f"contract:terminate_confirm:{contract_id}")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="contract:terminate_cancel")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "contract:terminate_cancel")
async def terminate_contract_cancel(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.regexp(r"^contract:terminate_confirm:\d+$"))
async def terminate_contract_confirm(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        contract_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    ok = await terminate_contract(callback.from_user.id, contract_id)
    if ok:
        text, kb = await _build_contract_view_text(callback.from_user.id, contract_id)
        await callback.message.answer(text, reply_markup=kb)
    else:
        await callback.message.answer("Не удалось прекратить договор (проверьте доступ).")
    await callback.answer()


@router.callback_query(F.data.regexp(r"^contract:mark_payment_start:\d+$"))
async def contract_mark_payment_start(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return

    try:
        contract_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    ct = await get_contract_detailed(callback.from_user.id, contract_id)
    if ct is None:
        await callback.answer("Договор не найден", show_alert=True)
        return

    pending: list[tuple[int, object]] = []
    for idx, p in enumerate(ct.payments, start=1):
        if p.status == PaymentStatus.pending:
            pending.append((idx, p))

    if not pending:
        await callback.answer("Нет ожидающих взносов", show_alert=True)
        return

    kb_rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for idx, p in pending:
        label = f"Взнос {idx}: {p.amount_minor/100:.2f} {ct.currency}"
        row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"contract:mark_payment_confirm:{p.id}",
            )
        )
        if len(row) >= 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)

    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"contract:mark_payment_back:{ct.id}")])

    await callback.message.answer("Выберите взнос:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await callback.answer()


@router.callback_query(F.data.startswith("contract:mark_payment_back:"))
async def contract_mark_payment_back(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return

    try:
        contract_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    text, kb = await _build_contract_view_text(callback.from_user.id, contract_id)
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^contract:mark_payment_confirm:\d+$"))
async def contract_mark_payment_confirm(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return

    try:
        payment_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    contract_id = await mark_payment_paid(callback.from_user.id, payment_id)
    if contract_id is None:
        await callback.answer("Не удалось отметить взнос", show_alert=True)
        return

    text, kb = await _build_contract_view_text(callback.from_user.id, contract_id)
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^client:delete:\d+$"))
async def delete_client_start(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        client_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    await callback.message.answer(
        "Удалить клиента и все его договоры/фото безвозвратно?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"client:delete_confirm:{client_id}")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="client:delete_cancel")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "client:delete_cancel")
async def delete_client_cancel(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.regexp(r"^client:delete_confirm:\d+$"))
async def delete_client_confirm(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        client_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    ok = await delete_client(callback.from_user.id, client_id)
    await callback.message.answer("✅ Клиент удалён." if ok else "Не удалось удалить (проверьте доступ).")
    if ok:
        _LAST_CLIENTS_BY_AGENT.pop(callback.from_user.id, None)
        # Refresh UI (client list might still include deleted client).
        if callback.message is not None:
            await open_clients_menu(callback.message, limit=20, agent_tg_id=callback.from_user.id)
    await callback.answer()

