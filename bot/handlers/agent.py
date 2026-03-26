import json
from io import BytesIO
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from openpyxl import Workbook

from bot.config import get_settings
from bot.db.models import ApplicationStatus, UserRole
from bot.db.repo import (
    create_agent_invite,
    create_application_note,
    create_reminder,
    delete_note_for_agent,
    delete_application,
    get_note_for_agent,
    get_or_create_public_agent_link,
    get_or_create_user,
    list_incoming_applications,
    list_in_progress_applications,
    list_invited_client_user_ids,
    list_notes_for_agent,
    list_notes_for_application,
    list_agent_commissions,
    list_agent_invites,
    list_agent_companies_for_commission,
    list_agent_contract_kinds_for_company,
    revoke_agent_invite,
    regenerate_public_agent_link,
    upsert_agent_commission,
    set_agent_password,
    set_application_status,
)
from bot.keyboards import Btn, agent_menu, application_actions_keyboard
from bot.services.datetime_parse import combine_local, parse_date_ru, parse_relative_ru, parse_time_ru
from bot.scheduler.payment_reminders import get_pending_payments_due_between
from bot.handlers.commission_reports import get_commission_rows_for_period
from bot.services.agent_auth import authorize_agent_session

router = Router()


class AppNoteCreate(StatesGroup):
    text = State()
    reminder_pick = State()
    reminder_date = State()
    reminder_time = State()


class AgentCommissionSetup(StatesGroup):
    add_company = State()
    add_kind = State()
    set_percent = State()


class AgentAuthSetup(StatesGroup):
    password = State()


def _safe_delete_text(text: str | None) -> str:
    if not text:
        return "—"
    return text[:120]


_COMMISSION_COMPANY_OPTIONS: dict[int, list[str]] = {}
_COMMISSION_KIND_OPTIONS: dict[tuple[int, str], list[str]] = {}


def _reports_types_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💸 Отчёты по взносам", callback_data="agent:reports:payments")],
            [InlineKeyboardButton(text="📅 Заканчивающиеся договоры", callback_data="agent:reports:contracts")],
            [InlineKeyboardButton(text="💼 Отчёты по комиссиям", callback_data="agent:reports:commissions")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="agent:reports_back")],
        ]
    )


def _payments_reports_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 день", callback_data="payrep:1"),
                InlineKeyboardButton(text="3 дня", callback_data="payrep:3"),
                InlineKeyboardButton(text="7 дней", callback_data="payrep:7"),
                InlineKeyboardButton(text="30 дней", callback_data="payrep:range:30"),
            ],
            [InlineKeyboardButton(text="⬅️ К типам отчётов", callback_data="agent:reports_types")],
        ]
    )


def _contracts_reports_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 день", callback_data="endrep:1"),
                InlineKeyboardButton(text="3 дня", callback_data="endrep:3"),
                InlineKeyboardButton(text="7 дней", callback_data="endrep:7"),
                InlineKeyboardButton(text="30 дней", callback_data="endrep:30"),
            ],
            [InlineKeyboardButton(text="⬅️ К типам отчётов", callback_data="agent:reports_types")],
        ]
    )


def _commissions_reports_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 день", callback_data="comrep:1"),
                InlineKeyboardButton(text="3 дня", callback_data="comrep:3"),
                InlineKeyboardButton(text="7 дней", callback_data="comrep:7"),
                InlineKeyboardButton(text="30 дней", callback_data="comrep:30"),
            ],
            [InlineKeyboardButton(text="⬅️ К типам отчётов", callback_data="agent:reports_types")],
        ]
    )


def _settings_root_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💼 Комиссия", callback_data="aset:commission")],
            [InlineKeyboardButton(text="🔐 Доступ агента", callback_data="aset:auth")],
            [InlineKeyboardButton(text="🔗 Пригласить клиента", callback_data="aset:invite:create")],
            [InlineKeyboardButton(text="🌐 Публичная ссылка", callback_data="aset:public_link")],
            [InlineKeyboardButton(text="📎 Мои инвайты", callback_data="aset:invite:list")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="aset:back")],
        ]
    )


def _commission_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏢 Страховые компании", callback_data="acom:companies")],
            [InlineKeyboardButton(text="📄 Посмотреть комиссии", callback_data="acom:view")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="aset:root")],
        ]
    )


def _commissions_to_xlsx_bytes(rows) -> tuple[str, bytes]:
    wb = Workbook()
    ws = wb.active
    ws.title = "Комиссии"
    ws.append(["Компания", "Вид страхования", "Комиссия, %"])
    last_company = None
    for r in rows:
        if r.company != last_company:
            ws.append([r.company, "", ""])
            last_company = r.company
        ws.append(["", r.contract_kind, f"{r.percent_bp / 100:.2f}"])
    ws.column_dimensions["A"].width = 35
    ws.column_dimensions["B"].width = 35
    ws.column_dimensions["C"].width = 15
    bio = BytesIO()
    wb.save(bio)
    return "commissions.xlsx", bio.getvalue()


async def _ensure_agent(message: Message) -> bool:
    user = await get_or_create_user(message.from_user.id)
    return user.role == UserRole.agent


async def _ensure_agent_tg(tg_id: int) -> bool:
    user = await get_or_create_user(tg_id)
    return user.role == UserRole.agent


@router.message(F.text == Btn.INCOMING)
async def agent_incoming(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    apps = await list_incoming_applications(message.from_user.id)
    invited_user_ids = await list_invited_client_user_ids(message.from_user.id)
    if not apps:
        await message.answer("Новых заявок нет.", reply_markup=agent_menu())
        return
    for a in apps:
        client_tg = a.client.tg_id if a.client is not None else "?"
        invited_mark = "\n🔗 Клиент: по инвайту" if (a.client is not None and a.client.id in invited_user_ids) else ""
        header = f"📌 Заявка №{a.id}\n👤 Клиент: tg_id={client_tg}{invited_mark}\n📎 Статус: {a.status.value}"

        details = ""
        if a.quote is not None:
            premium = a.quote.premium_amount / 100.0
            try:
                payload = json.loads(a.quote.input_json)
            except Exception:
                payload = {}
            if a.quote.quote_type.value == "kasko":
                name = payload.get("full_name")
                contact = payload.get("contact")
                bm = payload.get("brand_model", "?")
                year = payload.get("year", "?")
                car_value = payload.get("car_value", "?")
                abroad = "да" if payload.get("abroad") else "нет"
                drivers = payload.get("drivers_count", "?")
                age = payload.get("youngest_driver_age", "?")
                details = (
                    "\n🧮 КАСКО расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Авто: {bm} ({year})\n"
                    + f"- Стоимость: {car_value} BYN\n"
                    + f"- Заграница: {abroad}\n"
                    + f"- Водителей: {drivers}, мин. возраст: {age}\n"
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            elif a.quote.quote_type.value == "property":
                name = payload.get("full_name")
                contact = payload.get("contact")
                subject = payload.get("subject", "?")
                loc = payload.get("address_or_city", "?")
                value = payload.get("property_value", "?")
                comment = payload.get("comment")
                details = (
                    "\n🧮 Имущество расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Что страхуем: {subject}\n"
                    + f"- Локация: {loc}\n"
                    + f"- Стоимость: {value} BYN\n"
                    + (f"- Комментарий: {comment}\n" if comment else "")
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            elif a.quote.quote_type.value == "accident":
                name = payload.get("full_name")
                contact = payload.get("contact")
                details = (
                    "\n🧮 ✈️ Страховка за границу расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Срок поездки: {payload.get('days', '?')} дней\n"
                    + f"- Возраст: {payload.get('age', '?')}\n"
                    + f"- Вариант A: {'да' if payload.get('variant_a') else 'нет'}"
                    + (
                        f" (сумма {payload.get('sum_a')})\n"
                        if payload.get("variant_a")
                        else "\n"
                    )
                    + f"- Вариант B: {'да' if payload.get('variant_b') else 'нет'}"
                    + (
                        f" (сумма {payload.get('sum_b')})\n"
                        if payload.get("variant_b")
                        else "\n"
                    )
                    + f"- Территория AB5: вариант {payload.get('ab5_option', '?')}\n"
                    + f"- Спорт (A2): {'да' if payload.get('sport_training') else 'нет'}\n"
                    + f"- Кол-во застрахованных: {payload.get('insured_count', '?')}\n"
                    + f"- Повторный договор (AB3.1): {'да' if payload.get('repeat_contract') else 'нет'}\n"
                    + f"- Электронный полис (AB3.2): {'да' if payload.get('e_policy') else 'нет'}\n"
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            elif a.quote.quote_type.value == "expeditor":
                name = payload.get("full_name")
                contact = payload.get("contact")
                details = (
                    "\n🧮 🚛 Ответственность экспедитора расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Пакет: {payload.get('plan_title', '?')}\n"
                    + f"- Лимит на случай: {payload.get('per_case_limit', '?')} USD/EUR\n"
                    + f"- Агрегатный лимит: {payload.get('aggregate_limit', '?')} USD/EUR\n"
                    + f"- Франшиза: {payload.get('franchise', '?')} USD/EUR\n"
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            elif a.quote.quote_type.value in {"cargo", "cmr", "dms", "other"}:
                name = payload.get("full_name")
                contact = payload.get("contact")
                subject = payload.get("subject", "?")
                value = payload.get("insured_value", "?")
                comment = payload.get("comment")
                extra = payload.get("extra_type")
                kind_map = {
                    "cargo": "📦 Грузы",
                    "accident": "✈️ Страховка за границу",
                    "cmr": "🚚 CMR",
                    "dms": "🩺 ДМС",
                    "other": "✍️ Другой вид",
                }
                kind_title = kind_map.get(a.quote.quote_type.value, "✍️ Другой вид")
                if a.quote.quote_type.value == "other" and extra:
                    kind_title = f"{kind_title} ({extra})"
                details = (
                    f"\n🧮 {kind_title} расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Что страхуем: {subject}\n"
                    + f"- Стоимость: {value} BYN\n"
                    + (f"- Комментарий: {comment}\n" if comment else "")
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            else:
                details = f"\n🧮 Расчёт: {premium:.2f} {a.quote.currency}\n- Quote ID: {a.quote.id}"

        desc = f"\n📝 Комментарий: {a.description}" if a.description else ""
        await message.answer(header + details + desc, reply_markup=application_actions_keyboard(a.id, in_progress=False))

    await message.answer("Выберите действие.", reply_markup=agent_menu())


@router.message(F.text == Btn.IN_PROGRESS)
async def agent_in_progress(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    apps = await list_in_progress_applications(message.from_user.id)
    invited_user_ids = await list_invited_client_user_ids(message.from_user.id)
    if not apps:
        await message.answer("Заявок в работе нет.", reply_markup=agent_menu())
        return
    for a in apps:
        client_tg = a.client.tg_id if a.client is not None else "?"
        invited_mark = "\n🔗 Клиент: по инвайту" if (a.client is not None and a.client.id in invited_user_ids) else ""
        header = f"🛠 В работе №{a.id}\n👤 Клиент: tg_id={client_tg}{invited_mark}\n📎 Статус: {a.status.value}"

        details = ""
        if a.quote is not None:
            premium = a.quote.premium_amount / 100.0
            try:
                payload = json.loads(a.quote.input_json)
            except Exception:
                payload = {}
            if a.quote.quote_type.value == "kasko":
                name = payload.get("full_name")
                contact = payload.get("contact")
                bm = payload.get("brand_model", "?")
                year = payload.get("year", "?")
                car_value = payload.get("car_value", "?")
                abroad = "да" if payload.get("abroad") else "нет"
                drivers = payload.get("drivers_count", "?")
                age = payload.get("youngest_driver_age", "?")
                details = (
                    "\n🧮 КАСКО расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Авто: {bm} ({year})\n"
                    + f"- Стоимость: {car_value} BYN\n"
                    + f"- Заграница: {abroad}\n"
                    + f"- Водителей: {drivers}, мин. возраст: {age}\n"
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            elif a.quote.quote_type.value == "property":
                name = payload.get("full_name")
                contact = payload.get("contact")
                subject = payload.get("subject", "?")
                loc = payload.get("address_or_city", "?")
                value = payload.get("property_value", "?")
                comment = payload.get("comment")
                details = (
                    "\n🧮 Имущество расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Что страхуем: {subject}\n"
                    + f"- Локация: {loc}\n"
                    + f"- Стоимость: {value} BYN\n"
                    + (f"- Комментарий: {comment}\n" if comment else "")
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            elif a.quote.quote_type.value == "accident":
                name = payload.get("full_name")
                contact = payload.get("contact")
                details = (
                    "\n🧮 ✈️ Страховка за границу расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Срок поездки: {payload.get('days', '?')} дней\n"
                    + f"- Возраст: {payload.get('age', '?')}\n"
                    + f"- Вариант A: {'да' if payload.get('variant_a') else 'нет'}"
                    + (
                        f" (сумма {payload.get('sum_a')})\n"
                        if payload.get("variant_a")
                        else "\n"
                    )
                    + f"- Вариант B: {'да' if payload.get('variant_b') else 'нет'}"
                    + (
                        f" (сумма {payload.get('sum_b')})\n"
                        if payload.get("variant_b")
                        else "\n"
                    )
                    + f"- Территория AB5: вариант {payload.get('ab5_option', '?')}\n"
                    + f"- Спорт (A2): {'да' if payload.get('sport_training') else 'нет'}\n"
                    + f"- Кол-во застрахованных: {payload.get('insured_count', '?')}\n"
                    + f"- Повторный договор (AB3.1): {'да' if payload.get('repeat_contract') else 'нет'}\n"
                    + f"- Электронный полис (AB3.2): {'да' if payload.get('e_policy') else 'нет'}\n"
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            elif a.quote.quote_type.value == "expeditor":
                name = payload.get("full_name")
                contact = payload.get("contact")
                details = (
                    "\n🧮 🚛 Ответственность экспедитора расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Пакет: {payload.get('plan_title', '?')}\n"
                    + f"- Лимит на случай: {payload.get('per_case_limit', '?')} USD/EUR\n"
                    + f"- Агрегатный лимит: {payload.get('aggregate_limit', '?')} USD/EUR\n"
                    + f"- Франшиза: {payload.get('franchise', '?')} USD/EUR\n"
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            elif a.quote.quote_type.value in {"cargo", "cmr", "dms", "other"}:
                name = payload.get("full_name")
                contact = payload.get("contact")
                subject = payload.get("subject", "?")
                value = payload.get("insured_value", "?")
                comment = payload.get("comment")
                extra = payload.get("extra_type")
                kind_map = {
                    "cargo": "📦 Грузы",
                    "accident": "✈️ Страховка за границу",
                    "cmr": "🚚 CMR",
                    "dms": "🩺 ДМС",
                    "other": "✍️ Другой вид",
                }
                kind_title = kind_map.get(a.quote.quote_type.value, "✍️ Другой вид")
                if a.quote.quote_type.value == "other" and extra:
                    kind_title = f"{kind_title} ({extra})"
                details = (
                    f"\n🧮 {kind_title} расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Что страхуем: {subject}\n"
                    + f"- Стоимость: {value} BYN\n"
                    + (f"- Комментарий: {comment}\n" if comment else "")
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            else:
                details = f"\n🧮 Расчёт: {premium:.2f} {a.quote.currency}\n- Quote ID: {a.quote.id}"

        notes = await list_notes_for_application(message.from_user.id, a.id, limit=1)
        has_notes = len(notes) > 0
        if has_notes:
            header += f"\n🗒 Заметки: {len(await list_notes_for_application(message.from_user.id, a.id, limit=50))}"
        desc = f"\n📝 Комментарий: {a.description}" if a.description else ""
        await message.answer(
            header + details + desc,
            reply_markup=application_actions_keyboard(a.id, in_progress=True, has_notes=has_notes),
        )

    await message.answer(
        "Заметки по заявкам:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="📒 Открыть заметки", callback_data="app:notes:all")]]
        ),
    )
    await message.answer("Выберите действие.", reply_markup=agent_menu())


@router.callback_query(F.data.startswith("app:take:"))
async def app_take(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not (await _ensure_agent_tg(callback.from_user.id)):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        app_id = int(callback.data.split(":")[-1])
    except Exception:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    app = await set_application_status(callback.from_user.id, app_id, status=ApplicationStatus.in_progress)
    if app is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(f"✅ Заявка №{app_id} взята в работу.")
    callback.data = f"app:open:{app_id}"
    await app_open_one(callback)


@router.callback_query(F.data.startswith("app_status:"))
async def app_status_legacy(callback: CallbackQuery) -> None:
    # Backward compatibility for old inline keyboards still visible in chat history.
    if callback.message is None:
        await callback.answer()
        return
    try:
        _, app_id_s, status_s = callback.data.split(":", 2)
        app_id = int(app_id_s)
    except Exception:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    if status_s == "in_progress":
        callback.data = f"app:take:{app_id}"
        await app_take(callback)
        return
    if status_s == "done":
        callback.data = f"app:delete:{app_id}"
        await app_delete(callback)
        return
    await callback.answer("Некорректный статус", show_alert=True)


@router.callback_query(F.data.startswith("app:delete:"))
async def app_delete(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not (await _ensure_agent_tg(callback.from_user.id)):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        app_id = int(callback.data.split(":")[-1])
    except Exception:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    app = await delete_application(callback.from_user.id, app_id)
    if app is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(f"🗑 Заявка №{app_id} удалена.")
    await callback.answer("Удалено")


@router.callback_query(F.data.startswith("app:note:add:"))
async def app_note_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not (await _ensure_agent_tg(callback.from_user.id)):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        app_id = int(callback.data.split(":")[-1])
    except Exception:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    await state.clear()
    await state.update_data(note_app_id=app_id)
    await state.set_state(AppNoteCreate.text)
    await callback.message.answer(f"Введите заметку для заявки №{app_id}:")
    await callback.answer()


@router.message(AppNoteCreate.text)
async def app_note_add_text(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    text = (message.text or "").strip()
    if len(text) < 2:
        await message.answer("Слишком коротко. Введите текст заметки.")
        return
    data = await state.get_data()
    app_id = int(data["note_app_id"])
    note = await create_application_note(message.from_user.id, app_id, text)
    if note is None:
        await state.clear()
        await message.answer("Не удалось сохранить заметку. Заявка не найдена или не в работе.")
        return
    await state.update_data(note_id=note.id, note_text=text)
    await state.set_state(AppNoteCreate.reminder_pick)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏰ Да, создать", callback_data="app:note:reminder:yes"),
                InlineKeyboardButton(text="Нет", callback_data="app:note:reminder:no"),
            ]
        ]
    )
    await message.answer(f"✅ Заметка #{note.id} сохранена для заявки №{app_id}. Создать напоминание?", reply_markup=kb)


@router.callback_query(F.data.in_({"app:note:reminder:yes", "app:note:reminder:no"}))
async def app_note_reminder_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if callback.data.endswith(":no"):
        await state.clear()
        await callback.message.answer("Ок, без напоминания.")
        await callback.answer()
        return
    await state.set_state(AppNoteCreate.reminder_date)
    await callback.message.answer("Дата напоминания? (например: завтра / 20.03.2026)")
    await callback.answer()


@router.message(AppNoteCreate.reminder_date)
async def app_note_reminder_date(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    res = parse_date_ru(message.text or "", today=datetime.now(tz).date())
    if res is None:
        await message.answer("Не понял дату. Пример: завтра / 20.03.2026")
        return
    await state.update_data(rem_date_iso=res.target_date.isoformat())
    await state.set_state(AppNoteCreate.reminder_time)
    await message.answer("Время? Например: 14:30")


@router.message(AppNoteCreate.reminder_time)
async def app_note_reminder_time(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    data = await state.get_data()
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz)
    rel = parse_relative_ru(message.text or "", now_local=now_local)
    if rel is not None:
        remind_local = rel
    else:
        t = parse_time_ru(message.text or "")
        if t is None:
            await message.answer("Не понял время. Примеры: 14:30 / через 1 минуту / через час")
            return
        d = datetime.fromisoformat(str(data["rem_date_iso"])).date()
        remind_local = combine_local(d, t, now_local=now_local)
    note_id = int(data["note_id"])
    app_id = int(data["note_app_id"])
    note_text = _safe_delete_text(str(data.get("note_text") or ""))
    r = await create_reminder(
        message.from_user.id,
        text_value=f"Заметка к заявке №{app_id}: {note_text}",
        remind_at_utc=remind_local.astimezone(timezone.utc),
        note_id=note_id,
    )
    await state.clear()
    await message.answer(f"✅ Напоминание #{r.id} создано для заметки #{note_id}.")


@router.callback_query(F.data == "app:notes:all")
async def app_notes_all(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not (await _ensure_agent_tg(callback.from_user.id)):
        await callback.answer("Недоступно", show_alert=True)
        return
    notes = await list_notes_for_agent(callback.from_user.id, limit=30)
    if not notes:
        await callback.message.answer("Заметок пока нет.")
        await callback.answer()
        return
    for n in notes:
        app_id = n.application_id
        text = f"🗒 Заметка #{n.id} к заявке №{app_id}\n{_safe_delete_text(n.text)}"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"Открыть заявку №{app_id}", callback_data=f"app:open:{app_id}")],
                [InlineKeyboardButton(text="🗑 Удалить заметку", callback_data=f"app:note:delete:{n.id}")],
            ]
        )
        await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("app:note:list:"))
async def app_notes_for_one(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not (await _ensure_agent_tg(callback.from_user.id)):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        app_id = int(callback.data.split(":")[-1])
    except Exception:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    notes = await list_notes_for_application(callback.from_user.id, app_id, limit=20)
    if not notes:
        await callback.message.answer("К этой заявке заметок пока нет.")
        await callback.answer()
        return
    for n in notes:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"Открыть заявку №{app_id}", callback_data=f"app:open:{app_id}")],
                [InlineKeyboardButton(text="🗑 Удалить заметку", callback_data=f"app:note:delete:{n.id}")],
            ]
        )
        await callback.message.answer(f"🗒 Заметка #{n.id}\n{n.text}", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("app:note:delete:"))
async def app_note_delete(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not (await _ensure_agent_tg(callback.from_user.id)):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        note_id = int(callback.data.split(":")[-1])
    except Exception:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    ok = await delete_note_for_agent(callback.from_user.id, note_id)
    if not ok:
        await callback.answer("Заметка не найдена", show_alert=True)
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(f"🗑 Заметка #{note_id} удалена.")
    await callback.answer("Удалено")


@router.callback_query(F.data.startswith("app:open:"))
async def app_open_one(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not (await _ensure_agent_tg(callback.from_user.id)):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        app_id = int(callback.data.split(":")[-1])
    except Exception:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    apps = await list_incoming_applications(callback.from_user.id, limit=100) + await list_in_progress_applications(
        callback.from_user.id, limit=100
    )
    app = next((a for a in apps if a.id == app_id), None)
    if app is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    status_title = "📌" if app.status == ApplicationStatus.new else "🛠"
    text = f"{status_title} Заявка №{app.id}\nСтатус: {app.status.value}\n\n{app.description or 'Без описания'}"
    notes = await list_notes_for_application(callback.from_user.id, app.id, limit=1)
    await callback.message.answer(text, reply_markup=application_actions_keyboard(app.id, in_progress=app.status == ApplicationStatus.in_progress, has_notes=bool(notes)))
    await callback.answer()


@router.callback_query(F.data.startswith("rem:note:"))
async def open_note_from_reminder(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not (await _ensure_agent_tg(callback.from_user.id)):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        note_id = int(callback.data.split(":")[-1])
    except Exception:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    note = await get_note_for_agent(callback.from_user.id, note_id)
    if note is None:
        await callback.answer("Заметка не найдена", show_alert=True)
        return
    app_id = note.application_id
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=f"Открыть заявку №{app_id}", callback_data=f"app:open:{app_id}")]]
    )
    await callback.message.answer(f"🗒 Заметка #{note.id} к заявке №{app_id}\n{note.text}", reply_markup=kb)
    await callback.answer()


@router.message(F.text == Btn.MY_CLIENTS)
async def agent_clients(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    from bot.handlers.clients import open_clients_menu

    await open_clients_menu(message)


@router.message(F.text == Btn.DASHBOARD)
async def agent_dashboard(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    today = datetime.now(tz).date()
    incoming = await list_incoming_applications(message.from_user.id, limit=1000)
    in_progress = await list_in_progress_applications(message.from_user.id, limit=1000)
    overdue = await get_pending_payments_due_between(message.from_user.id, today - timedelta(days=3650), today - timedelta(days=1))
    upcoming7 = await get_pending_payments_due_between(message.from_user.id, today, today + timedelta(days=7))
    com_today = await get_commission_rows_for_period(message.from_user.id, today, today, tz)
    com_7 = await get_commission_rows_for_period(message.from_user.id, today - timedelta(days=6), today, tz)
    com_30 = await get_commission_rows_for_period(message.from_user.id, today - timedelta(days=29), today, tz)
    txt = (
        "📈 Дашборд агента\n"
        f"🗓 Дата: {today:%d.%m.%Y}\n\n"
        f"📥 Входящие заявки: {len(incoming)}\n"
        f"🛠 Заявки в работе: {len(in_progress)}\n"
        f"⛔ Просроченные взносы: {len(overdue)}\n"
        f"⏳ Взносы в ближайшие 7 дней: {len(upcoming7)}\n\n"
        f"💼 Комиссия за сегодня: {sum(r.commission_minor for r in com_today)/100.0:.2f}\n"
        f"💼 Комиссия за 7 дней: {sum(r.commission_minor for r in com_7)/100.0:.2f}\n"
        f"💼 Комиссия за 30 дней: {sum(r.commission_minor for r in com_30)/100.0:.2f}"
    )
    await message.answer(txt, reply_markup=agent_menu())

@router.message(F.text == Btn.REPORTS)
async def agent_reports(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    await message.answer("Выберите тип отчёта:", reply_markup=_reports_types_keyboard())


@router.callback_query(F.data == "agent:reports_types")
async def agent_reports_types(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await callback.message.answer("Выберите тип отчёта:", reply_markup=_reports_types_keyboard())
    await callback.answer()


@router.callback_query(F.data == "agent:reports:payments")
async def agent_reports_payments(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await callback.message.answer("Отчеты по взносам в ближайшие:", reply_markup=_payments_reports_keyboard())
    await callback.answer()


@router.callback_query(F.data == "agent:reports:contracts")
async def agent_reports_contracts(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await callback.message.answer(
        "Отчеты по заканчивающимся договорам в ближайшие:",
        reply_markup=_contracts_reports_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "agent:reports:commissions")
async def agent_reports_commissions(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await callback.message.answer(
        "Отчеты по комиссиям за последние:",
        reply_markup=_commissions_reports_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "agent:reports_back")
async def agent_reports_back(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await callback.message.answer("Главное меню", reply_markup=agent_menu())
    await callback.answer()


@router.message(F.text == Btn.SETTINGS)
async def agent_settings(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    await message.answer("⚙️ Настройки:", reply_markup=_settings_root_keyboard())


@router.callback_query(F.data == "aset:root")
async def settings_root(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await callback.message.answer("⚙️ Настройки:", reply_markup=_settings_root_keyboard())
    await callback.answer()


@router.callback_query(F.data == "aset:commission")
async def settings_commission(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await callback.message.answer("💼 Комиссии:", reply_markup=_commission_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "aset:auth")
async def settings_auth(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await state.clear()
    await state.set_state(AgentAuthSetup.password)
    await callback.message.answer("Введите новый пароль агента (минимум 6 символов):")
    await callback.answer()


@router.callback_query(F.data == "aset:invite:create")
async def settings_invite_create(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    inv = await create_agent_invite(callback.from_user.id, ttl_hours=72, uses_left=1)
    if inv is None:
        await callback.answer("Не удалось создать инвайт", show_alert=True)
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
        "🔗 Ссылка для клиента (действует 72 часа, 1 использование):\n"
        f"{link}\n\n"
        "Клиент открывает ссылку и автоматически привязывается к вашему пространству данных."
    )
    await callback.answer("Инвайт создан")


@router.callback_query(F.data == "aset:public_link")
async def settings_public_link(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    inv = await get_or_create_public_agent_link(callback.from_user.id)
    if inv is None:
        await callback.answer("Не удалось получить ссылку", show_alert=True)
        return
    token = f"public_{inv.token}"
    link = f"/start {token}"
    try:
        me = await callback.bot.get_me()
        if me.username:
            link = f"https://t.me/{me.username}?start={token}"
    except Exception:
        pass
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="♻️ Обновить ссылку", callback_data="aset:public_link:regen")],
            [InlineKeyboardButton(text="⬅️ К настройкам", callback_data="aset:root")],
        ]
    )
    await callback.message.answer(
        "🌐 Публичная ссылка агента (для соцсетей):\n"
        f"{link}\n\n"
        "Новые клиенты по этой ссылке автоматически привязываются к вам.",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data == "aset:public_link:regen")
async def settings_public_link_regen(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    inv = await regenerate_public_agent_link(callback.from_user.id)
    if inv is None:
        await callback.answer("Не удалось обновить ссылку", show_alert=True)
        return
    token = f"public_{inv.token}"
    link = f"/start {token}"
    try:
        me = await callback.bot.get_me()
        if me.username:
            link = f"https://t.me/{me.username}?start={token}"
    except Exception:
        pass
    await callback.message.answer(f"✅ Новая публичная ссылка:\n{link}")
    await callback.answer("Ссылка обновлена")


@router.callback_query(F.data == "aset:invite:list")
async def settings_invite_list(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    invites = await list_agent_invites(callback.from_user.id, limit=20)
    if not invites:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать инвайт", callback_data="aset:invite:create")],
                [InlineKeyboardButton(text="⬅️ К настройкам", callback_data="aset:root")],
            ]
        )
        await callback.message.answer("Пока нет инвайтов.", reply_markup=kb)
        await callback.answer()
        return

    rows: list[list[InlineKeyboardButton]] = []
    lines: list[str] = ["📎 Последние инвайты:"]
    for inv in invites:
        token_short = inv.token[:8]
        status_label = {
            "active": "активен",
            "used": "использован",
            "revoked": "отозван",
            "expired": "истек",
        }.get(inv.status.value, inv.status.value)
        lines.append(f"• #{inv.id} [{status_label}] token={token_short}… uses_left={inv.uses_left}")
        if inv.status.value == "active":
            rows.append([InlineKeyboardButton(text=f"🗑 Отозвать #{inv.id}", callback_data=f"aset:invite:revoke:{inv.id}")])
    rows.append([InlineKeyboardButton(text="➕ Создать инвайт", callback_data="aset:invite:create")])
    rows.append([InlineKeyboardButton(text="⬅️ К настройкам", callback_data="aset:root")])
    await callback.message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("aset:invite:revoke:"))
async def settings_invite_revoke(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        invite_id = int(callback.data.split(":")[-1])
    except Exception:
        await callback.answer("Некорректный id", show_alert=True)
        return
    ok = await revoke_agent_invite(callback.from_user.id, invite_id)
    if not ok:
        await callback.answer("Не удалось отозвать", show_alert=True)
        return
    await callback.answer("Инвайт отозван")
    await settings_invite_list(callback)


@router.callback_query(F.data == "aset:back")
async def settings_back(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await callback.message.answer("Выберите действие.", reply_markup=agent_menu())
    await callback.answer()


@router.callback_query(F.data == "acom:companies")
async def commission_companies(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    companies = await list_agent_companies_for_commission(callback.from_user.id, limit=100)
    _COMMISSION_COMPANY_OPTIONS[callback.from_user.id] = companies
    rows: list[list[InlineKeyboardButton]] = []
    for idx, c in enumerate(companies):
        rows.append([InlineKeyboardButton(text=c, callback_data=f"acom:company_pick:{idx}")])
    rows.append([InlineKeyboardButton(text="➕ Добавить компанию", callback_data="acom:company:add")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="aset:commission")])
    await callback.message.answer("🏢 Страховые компании:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "acom:company:add")
async def commission_company_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await state.clear()
    await state.set_state(AgentCommissionSetup.add_company)
    await callback.message.answer("Введите название страховой компании:")
    await callback.answer()


@router.callback_query(F.data.startswith("acom:company_pick:"))
async def commission_company_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        idx = int(callback.data.split(":")[-1])
    except Exception:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    company_options = _COMMISSION_COMPANY_OPTIONS.get(callback.from_user.id, [])
    if idx < 0 or idx >= len(company_options):
        await callback.answer("Список устарел. Откройте компании снова.", show_alert=True)
        return
    company = company_options[idx]
    await state.update_data(comm_company=company)
    kinds = await list_agent_contract_kinds_for_company(callback.from_user.id, company, limit=100)
    _COMMISSION_KIND_OPTIONS[(callback.from_user.id, company)] = kinds
    rows: list[list[InlineKeyboardButton]] = []
    for idx, k in enumerate(kinds):
        rows.append([InlineKeyboardButton(text=k, callback_data=f"acom:kind_pick:{idx}")])
    rows.append([InlineKeyboardButton(text="➕ Добавить новый вид", callback_data="acom:kind:add")])
    rows.append([InlineKeyboardButton(text="⬅️ К компаниям", callback_data="acom:companies")])
    await callback.message.answer(
        f"Виды страхования для «{company}»:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.message(AgentCommissionSetup.add_company)
async def commission_add_company(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    company = (message.text or "").strip()
    if len(company) < 2:
        await message.answer("Введите название компании (минимум 2 символа).")
        return
    await state.update_data(comm_company=company)
    await state.set_state(AgentCommissionSetup.add_kind)
    await message.answer(f"Введите вид страхования для «{company}»:")


@router.callback_query(F.data == "acom:kind:add")
async def commission_kind_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await state.set_state(AgentCommissionSetup.add_kind)
    await callback.message.answer("Введите новый вид страхования:")
    await callback.answer()


@router.callback_query(F.data.startswith("acom:kind_pick:"))
async def commission_kind_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        idx = int(callback.data.split(":")[-1])
    except Exception:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    data = await state.get_data()
    company = str(data.get("comm_company") or "")
    kinds = _COMMISSION_KIND_OPTIONS.get((callback.from_user.id, company), [])
    if idx < 0 or idx >= len(kinds):
        await callback.answer("Список устарел. Откройте виды заново.", show_alert=True)
        return
    kind = kinds[idx]
    await state.update_data(comm_kind=kind)
    await state.set_state(AgentCommissionSetup.set_percent)
    await callback.message.answer("Введите комиссию в процентах (например: 12.5):")
    await callback.answer()


@router.message(AgentCommissionSetup.add_kind)
async def commission_add_kind(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    kind = (message.text or "").strip()
    if len(kind) < 2:
        await message.answer("Введите вид страхования (минимум 2 символа).")
        return
    await state.update_data(comm_kind=kind)
    await state.set_state(AgentCommissionSetup.set_percent)
    await message.answer("Введите комиссию в процентах (например: 12.5):")


@router.message(AgentCommissionSetup.set_percent)
async def commission_set_percent(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip().replace(",", ".")
    try:
        percent = float(txt)
    except ValueError:
        await message.answer("Не понял число. Пример: 12.5")
        return
    if percent < 0 or percent > 100:
        await message.answer("Комиссия должна быть от 0 до 100.")
        return
    data = await state.get_data()
    company = str(data.get("comm_company") or "").strip()
    kind = str(data.get("comm_kind") or "").strip()
    if not company or not kind:
        await state.clear()
        await message.answer("Сессия устарела. Откройте настройки заново.")
        return
    percent_bp = int(round(percent * 100))
    row = await upsert_agent_commission(message.from_user.id, company=company, contract_kind=kind, percent_bp=percent_bp)
    await state.clear()
    if row is None:
        await message.answer("Не удалось сохранить комиссию.")
        return
    await message.answer(f"✅ Сохранено: {company} → {kind} = {percent:.2f}%")
    await message.answer("💼 Комиссии:", reply_markup=_commission_menu_keyboard())


@router.callback_query(F.data == "acom:view")
async def commission_view(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    rows = await list_agent_commissions(callback.from_user.id, limit=5000)
    if not rows:
        await callback.message.answer("Комиссий пока нет.")
        await callback.answer()
        return
    filename, payload = _commissions_to_xlsx_bytes(rows)
    try:
        await callback.message.answer_document(
            document=BufferedInputFile(payload, filename=filename),
            caption=f"📄 Комиссии: {len(rows)} записей",
        )
    except TelegramBadRequest:
        await callback.message.answer(f"Не удалось отправить файл. Записей: {len(rows)}")
    await callback.answer()


@router.message(AgentAuthSetup.password)
async def settings_auth_password(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    pwd = (message.text or "").strip()
    if len(pwd) < 6:
        await message.answer("Пароль слишком короткий. Минимум 6 символов.")
        return
    ok = await set_agent_password(message.from_user.id, pwd)
    await state.clear()
    if not ok:
        await message.answer("Не удалось сохранить пароль.")
        return
    authorize_agent_session(message.from_user.id)
    await message.answer("✅ Пароль агента сохранён.")
    await message.answer("⚙️ Настройки:", reply_markup=_settings_root_keyboard())
