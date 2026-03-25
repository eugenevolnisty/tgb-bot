from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from bot.db.models import Client, PaymentStatus, UserRole
from bot.db.repo import (
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
    list_clients,
    list_clients_page,
    list_contracts_for_client,
    list_client_documents,
    list_contract_documents,
    contract_has_documents,
    update_client,
    update_contract_for_client,
)
from bot.keyboards import agent_menu
from bot.services.datetime_parse import parse_date_ru

router = Router()

# In-memory mapping: last shown ordered list of clients per agent.
# Allows: agent opens "clients list" and then sends "1/2/3..." to open card.
_LAST_CLIENTS_BY_AGENT: dict[int, list[int]] = {}

# Used to refresh UI after deletion:
# when user opens a doc photo from the client/contract card,
# we remember the card message_id so after delete we can remove stale UI.
_CLIENT_DOC_CARD_MSG: dict[tuple[int, int], tuple[int, int]] = {}
# (agent_tg_id, doc_id) -> (client_id, card_message_id)

_CONTRACT_DOC_CARD_MSG: dict[tuple[int, int], tuple[int, int]] = {}
# (agent_tg_id, doc_id) -> (contract_id, card_message_id)


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
) -> None:
    tg_id = agent_tg_id if agent_tg_id is not None else message.from_user.id
    if not await _ensure_agent_tg(tg_id):
        return
    # Fetch one extra row to detect next page.
    items_page = await list_clients_page(tg_id, limit=limit + 1, offset=offset)
    if not items_page:
        await message.answer("Пока клиентов нет. Добавьте первого.", reply_markup=clients_menu_keyboard())
        return

    items = items_page[:limit]
    has_next = len(items_page) > limit
    has_prev = offset > 0

    lines = ["📋 Клиенты:"]
    # Store client ids in the same order we show them.
    _LAST_CLIENTS_BY_AGENT[tg_id] = [c.id for c in items]
    for i, c in enumerate(items, start=1):
        phone = f" • {c.phone}" if c.phone else ""
        lines.append(f"{i}) {c.full_name}{phone}")

    await message.answer("\n".join(lines), reply_markup=clients_menu_keyboard())

    # Separate message with inline pagination controls.
    if has_prev or has_next:
        kb_rows: list[list[InlineKeyboardButton]] = []
        if has_prev:
            kb_rows.append(
                [InlineKeyboardButton(text="⬅️ Предыдущая", callback_data=f"clients:page:{max(0, offset - limit)}")]
            )
        if has_next:
            kb_rows.append([InlineKeyboardButton(text="➡️ Следующая", callback_data=f"clients:page:{offset + limit}")])
        await message.answer("Листать список:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


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
    documents = await list_client_documents(agent_tg_id, client_id, limit=5)
    if exclude_client_document_ids:
        documents = [d for d in documents if d.id not in exclude_client_document_ids]

    lines = [
        f"📇 Клиент #{c.id}",
        f"👤 {c.full_name}",
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


@router.message(F.text.regexp(r"^\d+$"))
async def choose_client_by_index(message: Message, state: FSMContext) -> None:
    if await state.get_state() is not None:
        return
    if not await _ensure_agent(message):
        return
    ids = _LAST_CLIENTS_BY_AGENT.get(message.from_user.id)
    if not ids:
        return
    idx = int((message.text or "").strip()) - 1
    if idx < 0 or idx >= len(ids):
        await message.answer("Неверный номер. Введи номер из списка клиентов.", reply_markup=clients_menu_keyboard())
        return
    await _send_client_card(agent_tg_id=message.from_user.id, client_id=ids[idx], message=message)


@router.message(F.text == ClientsMenu.BACK)
async def clients_back(message: Message, state: FSMContext) -> None:
    if await state.get_state() is not None:
        await state.clear()
    _LAST_CLIENTS_BY_AGENT.pop(message.from_user.id, None)
    await message.answer("Меню.", reply_markup=agent_menu())


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
    start_date = State()
    end_date = State()
    total_amount = State()
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
    await message.answer("Имя клиента?", reply_markup=ReplyKeyboardRemove())


@router.message(F.text == ClientsMenu.SEARCH)
async def start_search_clients(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    await state.clear()
    await state.set_state(ClientsSearch.query)
    await message.answer("Введите текст для поиска (имя / телефон / email):", reply_markup=ReplyKeyboardRemove())


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
        f"👤 {c.full_name}",
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
        reply_markup=ReplyKeyboardRemove(),
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
    await callback.message.answer("Номер договора (например: 1-2026)?", reply_markup=ReplyKeyboardRemove())
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
    await callback.message.answer(f"Номер договора? Текущее: {contract.contract_number}\nНапишите новое.", reply_markup=ReplyKeyboardRemove())
    await callback.answer()


@router.message(ContractAdd.contract_number)
async def contract_step_number(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip()
    if len(txt) < 1:
        await message.answer("Введите номер договора.")
        return
    await state.update_data(contract_number=txt)
    await state.set_state(ContractAdd.company)
    await message.answer("Компания страхователя/компания-партнёр? (текст)")


@router.message(ContractAdd.company)
async def contract_step_company(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip()
    if len(txt) < 2:
        await message.answer("Введите компанию (минимум 2 символа).")
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
    await message.answer("Выбери вид договора:", reply_markup=kb)


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
        await callback.message.answer("Укажи конкретный вид договора (текстом).")
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
    await callback.message.answer("Выбери валюту договорa:", reply_markup=kb)
    await callback.answer()


@router.message(ContractAdd.contract_kind_other)
async def contract_step_kind_other(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip()
    if len(txt) < 2:
        await message.answer("Введите вид чуть подробнее.")
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
    await message.answer("Выбери валюту договора:", reply_markup=kb)


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
        await callback.message.answer(
            "Для КАСКО: какой автомобиль? (например: Toyota Camry 2020)",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await state.set_state(ContractAdd.start_date)
        await callback.message.answer(
            f"Дата начала договора? (например: 20.03.2026)\nВалюта: {currency}",
        )
    await callback.answer()


@router.message(ContractAdd.start_date)
async def contract_step_start_date(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    res = parse_date_ru(message.text or "", today=date.today())
    if res is None:
        await message.answer("Не понял дату. Пример: 20.03.2026")
        return
    await state.update_data(start_date_iso=res.target_date.isoformat())
    await state.set_state(ContractAdd.end_date)
    await message.answer("Дата окончания договора? (например: 20.03.2026)")


@router.message(ContractAdd.vehicle)
async def contract_step_vehicle(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip()
    if len(txt) < 3:
        await message.answer("Введите автомобиль чуть подробнее (минимум 3 символа).")
        return
    await state.update_data(contract_vehicle=txt)
    data = await state.get_data()
    currency = str(data.get("currency") or "BYN")
    await state.set_state(ContractAdd.start_date)
    await message.answer(
        f"Дата начала договора? (например: 20.03.2026)\nВалюта: {currency}",
    )


@router.message(ContractAdd.end_date)
async def contract_step_end_date(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    res = parse_date_ru(message.text or "", today=date.today())
    if res is None:
        await message.answer("Не понял дату. Пример: 20.03.2026")
        return
    await state.update_data(end_date_iso=res.target_date.isoformat())
    await state.set_state(ContractAdd.total_amount)
    data = await state.get_data()
    currency = str(data.get("currency") or "BYN")
    await message.answer(f"Сумма договора ({currency}), число. Например: 1500.50")


@router.message(ContractAdd.total_amount)
async def contract_step_total_amount(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        val = float(txt)
    except ValueError:
        await message.answer("Сумма должна быть числом. Например: 1500.50")
        return
    if val <= 0:
        await message.answer("Сумма должна быть больше 0.")
        return
    data = await state.get_data()
    currency = str(data.get("currency") or "BYN")
    await state.update_data(total_amount_minor=int(round(val * 100)), currency=currency)
    await state.set_state(ContractAdd.payments_count)
    await message.answer("График платежей: сколько платежей (1..24)?")


@router.message(ContractAdd.payments_count)
async def contract_step_payments_count(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    try:
        n = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введите число платежей, например: 3")
        return
    if n < 1 or n > 24:
        await message.answer("Количество платежей должно быть от 1 до 24.")
        return
    await state.update_data(payments_expected=n, payment_idx=0, payments=[])
    await state.set_state(ContractAdd.payment_amount)
    data = await state.get_data()
    currency = str(data.get("currency") or "BYN")
    await message.answer(f"Платёж 1: сумма ({currency}), число.")


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
    await message.answer(f"Платёж {idx + 1}: дата платежа (например: 20.03.2026)\nВалюта: {currency}")


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
        # Finish and persist.
        agent_id = message.from_user.id
        client_id = int(data["contract_add_client_id"])
        contract_id = data.get("contract_edit_id")
        contract_number = str(data["contract_number"])
        company = str(data["contract_company"])
        contract_kind = str(data["contract_kind"])
        vehicle_description = data.get("contract_vehicle")
        start_date_iso = str(data["start_date_iso"])
        end_date_iso = str(data["end_date_iso"])
        total_amount_minor = int(data["total_amount_minor"])
        currency = str(data.get("currency") or "BYN")

        payments_typed: list[tuple[int, date]] = [
            (p["amount_minor"], date.fromisoformat(p["due_iso"])) for p in payments
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
                currency=currency,
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
                currency=currency,
                vehicle_description=vehicle_description,
                payments=payments_typed,
            )

        await state.clear()
        if ct is None:
            await message.answer("Не удалось сохранить договор. Проверьте данные.", reply_markup=clients_menu_keyboard())
            return

        await message.answer(f"✅ Договор сохранён: #{ct.id} • {ct.contract_number}", reply_markup=clients_menu_keyboard())
        # Ask next action for contract docs / navigation.
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
        return

    await state.set_state(ContractAdd.payment_amount)
    currency_next = str(data.get("currency") or "BYN")
    await message.answer(f"Платёж {idx + 1}: сумма ({currency_next}), число.")


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

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Редактировать договор", callback_data=f"client:edit_contract:{ct.id}")],
            [InlineKeyboardButton(text="⬅️ Назад к клиенту", callback_data=f"client:open:{ct.client_id}")],
            [InlineKeyboardButton(text="📎 Добавить фото договора", callback_data=f"contract:add_doc:{ct.id}")],
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
        f"Сумма: {ct.total_amount_minor/100:.2f} {ct.currency}\n\n"
        f"График платежей:\n"
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

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Редактировать договор", callback_data=f"client:edit_contract:{ct.id}")],
            [InlineKeyboardButton(text="⬅️ Назад к клиенту", callback_data=f"client:open:{ct.client_id}")],
            [InlineKeyboardButton(text="📎 Добавить фото договора", callback_data=f"contract:add_doc:{ct.id}")],
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
        f"Сумма: {ct.total_amount_minor/100:.2f} {ct.currency}\n\n"
        f"График платежей:\n"
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
        reply_markup=ReplyKeyboardRemove(),
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
        reply_markup=ReplyKeyboardRemove(),
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

