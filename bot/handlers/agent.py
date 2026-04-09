import json
import uuid
from io import BytesIO
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from openpyxl import Workbook

from bot.config import get_settings
from bot.db.models import ApplicationStatus, InsuranceCompany, InsuranceType, UserRole
from bot.db.repo import (
    create_insurance_company,
    create_insurance_type,
    create_agent_invite,
    create_application_note,
    create_reminder,
    deactivate_insurance_company,
    deactivate_insurance_type,
    delete_note_for_agent,
    delete_application,
    get_insurance_company,
    get_insurance_type,
    get_note_for_agent,
    get_user_display_name,
    get_or_create_public_agent_link,
    get_or_create_user,
    get_agent_contacts,
    list_insurance_companies,
    list_insurance_types,
    list_tariff_cards,
    list_incoming_applications,
    list_in_progress_applications,
    list_invited_client_user_ids,
    list_notes_for_agent,
    list_notes_for_application,
    list_agent_commissions,
    list_agent_invites,
    list_agent_companies_for_commission,
    list_agent_contract_kinds_for_company,
    list_bound_client_tg_for_agent,
    revoke_agent_invite,
    regenerate_public_agent_link,
    upsert_agent_commission,
    set_agent_password,
    set_agent_display_name,
    set_agent_contacts,
    set_application_status,
    upsert_tariff_card,
)
from bot.keyboards import (
    Btn,
    _settings_agent_keyboard,
    _settings_clients_keyboard,
    _settings_root_keyboard,
    agent_menu,
    application_actions_keyboard,
)
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


class AgentProfileSetup(StatesGroup):
    name = State()
    contacts_phones = State()
    contacts_telegram = State()
    contacts_email = State()


class AgentBroadcast(StatesGroup):
    text = State()
    confirm = State()


class CompanySetup(StatesGroup):
    add_company_name = State()
    add_type_key = State()
    add_type_custom = State()


class TariffSetup(StatesGroup):
    base_type = State()
    base_single_rate = State()
    variant_name = State()
    variant_rate = State()
    variant_add_more = State()
    grade_by = State()
    grade_label = State()
    grade_item_label = State()
    grade_item_rate = State()
    grade_add_more = State()
    coeff_add_more = State()
    coeff_name = State()
    coeff_question = State()
    coeff_yes_value = State()
    coeff_no_value = State()
    coeff_edit_name = State()
    coeff_edit_question = State()
    coeff_edit_yes = State()
    coeff_edit_no = State()
    min_premium_ask = State()
    min_premium_amount = State()
    min_premium_currency = State()


KaskoTariffSetup = TariffSetup


INSURANCE_TYPE_KEYS = {
    "kasko": "🚗 КАСКО",
    "osago": "📋 ОСАГО",
    "property": "🏠 Имущество",
    "cargo": "📦 Грузы",
    "cmr": "🚛 CMR",
    "travel": "✈️ За границу",
    "expeditor": "🚚 Экспедитор",
    "dms": "🏥 ДМС",
    "other": "📋 Другой вид",
}


KASKO_COMMON_COEFFICIENTS = [
    {
        "name": "Противоугонная система",
        "question": "Есть противоугонная система?",
        "yes_value": 0.95,
        "no_value": 1.0,
    },
    {
        "name": "Франшиза",
        "question": "Есть франшиза?",
        "yes_value": 0.85,
        "no_value": 1.0,
    },
    {
        "name": "Рассрочка платежа",
        "question": "Оплата в рассрочку?",
        "yes_value": 1.05,
        "no_value": 1.0,
    },
    {
        "name": "Единовременная оплата",
        "question": "Единовременная оплата полной суммы?",
        "yes_value": 0.97,
        "no_value": 1.0,
    },
    {
        "name": "Кредит/Лизинг",
        "question": "Автомобиль в кредите или лизинге?",
        "yes_value": 1.05,
        "no_value": 1.0,
    },
    {
        "name": "Водитель до 25 лет",
        "question": "Есть водитель до 25 лет?",
        "yes_value": 1.15,
        "no_value": 1.0,
    },
    {
        "name": "Стаж менее 2 лет",
        "question": "Стаж вождения менее 2 лет?",
        "yes_value": 1.10,
        "no_value": 1.0,
    },
    {
        "name": "Один водитель",
        "question": "Только один водитель?",
        "yes_value": 0.97,
        "no_value": 1.0,
    },
    {
        "name": "До 5 водителей",
        "question": "До 5 водителей включительно?",
        "yes_value": 1.08,
        "no_value": 1.0,
    },
    {
        "name": "Ночная стоянка",
        "question": "Авто ночью на охраняемой стоянке?",
        "yes_value": 0.97,
        "no_value": 1.0,
    },
    {
        "name": "Новый автомобиль",
        "question": "Автомобиль новый (до 1 года)?",
        "yes_value": 1.05,
        "no_value": 1.0,
    },
    {
        "name": "Авто старше 7 лет",
        "question": "Автомобиль старше 7 лет?",
        "yes_value": 1.10,
        "no_value": 1.0,
    },
    {
        "name": "Коммерческое использование",
        "question": "Используется как такси или коммерчески?",
        "yes_value": 1.20,
        "no_value": 1.0,
    },
    {
        "name": "Электромобиль",
        "question": "Автомобиль электрический?",
        "yes_value": 1.10,
        "no_value": 1.0,
    },
    {
        "name": "Гибрид",
        "question": "Автомобиль гибридный?",
        "yes_value": 1.05,
        "no_value": 1.0,
    },
]

# Курсы валют (временные, до интеграции API)
# Для обновления — менять только здесь
EXCHANGE_RATES_TO_BYN = {
    "BYN": 1.0,
    "USD": 3.0,
    "EUR": 3.4,
    "RUB": 0.037,
    "CNY": 0.42,
}


class InsuranceCalc(StatesGroup):
    currency = State()
    ins_value = State()
    coefficients = State()
    result_shown = State()


class ClientNoCalcRequest(StatesGroup):
    comment = State()
    ins_value = State()
    year = State()


def _convert_to_byn(amount: float, currency: str) -> float:
    rate = EXCHANGE_RATES_TO_BYN.get(currency, 1.0)
    return amount * rate


def _format_amount(amount: float, currency: str) -> str:
    """
    Форматирует сумму.
    Если валюта BYN — без эквивалента.
    Иначе — с примерным эквивалентом в BYN.
    """
    if currency == "BYN":
        return f"{amount:,.2f} BYN"
    byn = _convert_to_byn(amount, currency)
    return (
        f"{amount:,.2f} {currency} "
        f"(примерно {byn:,.2f} BYN, "
        f"точные курсы будут интегрированы "
        f"позднее в обновлении)"
    )


def _calculate_premium(
    config: dict,
    ins_value: float,
    currency: str,
    coeff_answers: dict,
) -> dict:
    base = config.get("base", {}) or {}
    coefficients = config.get("coefficients", []) or []
    base_type = base.get("type", "single")

    base_rate = float(base.get("rate", 0.0) or 0.0)
    base_label = f"{base_rate}%"
    skip_first_coeff = False

    if base_type == "variants":
        skip_first_coeff = True
        variants = base.get("variants", []) or []
        idx = 0 if coeff_answers.get(coefficients[0]["id"], False) else 1 if coefficients and len(variants) > 1 else 0
        if isinstance(coeff_answers.get(coefficients[0]["id"]) if coefficients else None, int):
            idx = int(coeff_answers.get(coefficients[0]["id"]))  # type: ignore[arg-type]
        idx = min(max(idx, 0), max(len(variants) - 1, 0))
        variant = variants[idx] if variants else {}
        base_rate = float(variant.get("rate", 0.0) or 0.0)
        base_label = str(variant.get("name", f"{base_rate}%"))
    elif base_type == "graded":
        skip_first_coeff = True
        grades = base.get("grades", []) or []
        raw_idx = coeff_answers.get(coefficients[0]["id"], 0) if coefficients else 0
        if isinstance(raw_idx, bool):
            idx = int(raw_idx)
        else:
            try:
                idx = int(raw_idx)
            except Exception:
                idx = 0
        idx = min(max(idx, 0), max(len(grades) - 1, 0))
        grade = grades[idx] if grades else {}
        base_rate = float(grade.get("rate", 0.0) or 0.0)
        base_label = str(grade.get("label", f"{base_rate}%"))

    premium = ins_value * base_rate / 100.0

    applied_coeffs: list[dict] = []
    coeff_iter = coefficients[1:] if skip_first_coeff else coefficients
    for coeff in coeff_iter:
        answer = coeff_answers.get(coeff.get("id"), False)
        value = coeff.get("yes_value", 1.0) if answer else coeff.get("no_value", 1.0)
        value = float(value or 1.0)
        if value != 1.0:
            premium *= value
            applied_coeffs.append(
                {
                    "name": coeff.get("name", "Коэффициент"),
                    "value": value,
                    "answer": "Да" if answer else "Нет",
                }
            )

    premium_byn = _convert_to_byn(premium, currency)
    min_premium = config.get("min_premium", {}) or {}
    min_applied = False
    min_amount = None
    min_currency = None
    if min_premium.get("enabled"):
        min_amount = float(min_premium.get("amount", 0.0) or 0.0)
        min_currency = str(min_premium.get("currency", "BYN"))
        min_byn = _convert_to_byn(min_amount, min_currency)
        if premium_byn <= min_byn:
            min_applied = True
            if currency == min_currency:
                premium = min_amount
                premium_byn = _convert_to_byn(premium, currency)

    details = f"base={base_label}; coeffs={len(applied_coeffs)}; min_applied={min_applied}"
    return {
        "premium": premium,
        "currency": currency,
        "premium_byn": premium_byn,
        "base_rate": base_rate,
        "base_label": base_label,
        "applied_coeffs": applied_coeffs,
        "min_premium": min_amount,
        "min_currency": min_currency,
        "min_applied": min_applied,
        "details": details,
    }


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


def _commission_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏢 Страховые компании", callback_data="acom:companies")],
            [InlineKeyboardButton(text="📄 Посмотреть комиссии", callback_data="acom:view")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="aset:root")],
        ]
    )


def _companies_list_kb(companies: list[InsuranceCompany]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for company in companies:
        kb.button(text=company.name, callback_data=f"acomp:{company.id}")
    kb.button(text="💰 Комиссии", callback_data="aset:commission")
    kb.button(text="➕ Добавить компанию", callback_data="acomp:add")
    kb.button(text="← Назад", callback_data="aset:root")
    kb.adjust(*([1] * len(companies)), 1, 1, 1)
    return kb.as_markup()


def _company_detail_kb(company_id: int, types: list[InsuranceType]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for ins_type in types:
        display = ins_type.custom_name if ins_type.type_key == "other" else INSURANCE_TYPE_KEYS.get(ins_type.type_key, ins_type.type_key)
        kb.button(text=display, callback_data=f"acomp:type:{ins_type.id}")
    kb.button(text="➕ Добавить вид", callback_data=f"acomp:add_type:{company_id}")
    kb.button(text="🗑 Удалить компанию", callback_data=f"acomp:del:{company_id}")
    kb.button(text="← Назад", callback_data="aset:companies")
    kb.adjust(*([1] * len(types)), 1, 1, 1)
    return kb.as_markup()


def _type_keys_kb(company_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key, title in INSURANCE_TYPE_KEYS.items():
        kb.button(text=title, callback_data=f"acomp:type_key:{company_id}:{key}")
    kb.button(text="← Назад", callback_data=f"acomp:{company_id}")
    kb.adjust(2, 2, 2, 2, 1, 1)
    return kb.as_markup()


def _insurance_type_detail_kb(type_id: int, *, has_card: bool, has_other_cards: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🧮 Проверить расчёт", callback_data=f"acomp:calc:{type_id}")
    if has_card:
        kb.button(text="✏️ Редактировать расчёт", callback_data=f"acomp:tariff:edit:{type_id}")
        kb.button(text="📋 Копировать в...", callback_data=f"acomp:tariff:copy_from:{type_id}")
    else:
        kb.button(text="➕ Создать расчёт", callback_data=f"acomp:tariff:create:{type_id}")
        if has_other_cards:
            kb.button(text="📋 Скопировать из другой компании", callback_data=f"acomp:tariff:copy_from:{type_id}")
    kb.button(text="🗑 Удалить вид", callback_data=f"acomp:del_type:{type_id}")
    kb.button(text="📎 Добавить вложение", callback_data="acomp:soon")
    kb.button(text="← Назад", callback_data=f"acomp:type_back:{type_id}")
    kb.adjust(1)
    return kb.as_markup()


def _kt_cancel_kb(type_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=f"acomp:type:{type_id}")
    builder.adjust(1)
    return builder.as_markup()


def _kt_base_type_kb(type_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="1️⃣ Один тариф для всех", callback_data="kt:base:single")
    builder.button(text="2️⃣ Несколько вариантов", callback_data="kt:base:variants")
    builder.button(text="3️⃣ Зависит от параметра", callback_data="kt:base:graded")
    builder.button(text="❌ Отмена", callback_data=f"acomp:type:{type_id}")
    builder.adjust(1)
    return builder.as_markup()


def _kt_grade_by_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Возраст авто", callback_data="kt:grade_by:car_age")
    builder.button(text="📝 Другой параметр", callback_data="kt:grade_by:custom")
    builder.adjust(1)
    return builder.as_markup()


def _kt_variants_summary_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить вариант", callback_data="kt:variant:add")
    builder.button(text="✅ Готово", callback_data="kt:variant:done")
    builder.adjust(1)
    return builder.as_markup()


def _kt_grades_summary_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить градацию", callback_data="kt:grade:add")
    builder.button(text="✅ Готово", callback_data="kt:grade:done")
    builder.adjust(1)
    return builder.as_markup()


def _kt_coeff_start_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить коэффициент", callback_data="kt:coeff:add")
    builder.button(text="💡 Частые коэффициенты", callback_data="kt:coeff:common")
    builder.button(text="⏭ Пропустить", callback_data="kt:coeff:skip")
    builder.adjust(1)
    return builder.as_markup()


def _kt_coeff_summary_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить ещё", callback_data="kt:coeff:add")
    builder.button(text="✅ Готово", callback_data="kt:coeff:done")
    builder.adjust(1)
    return builder.as_markup()


def _kt_min_ask_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data="kt:min:yes")
    builder.button(text="❌ Нет", callback_data="kt:min:no")
    builder.adjust(2)
    return builder.as_markup()


def _kt_currency_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="BYN", callback_data="kt:min:cur:BYN")
    builder.button(text="USD", callback_data="kt:min:cur:USD")
    builder.button(text="EUR", callback_data="kt:min:cur:EUR")
    builder.adjust(3)
    return builder.as_markup()


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
    return user.role in {UserRole.agent, UserRole.superadmin}


async def _ensure_agent_tg(tg_id: int) -> bool:
    user = await get_or_create_user(tg_id)
    return user.role in {UserRole.agent, UserRole.superadmin}


@router.message(F.text == Btn.INCOMING)
async def agent_incoming(message: Message, data: dict) -> None:
    if not await _ensure_agent(message):
        return
    try:
        await message.delete()
    except Exception:
        pass
    is_superadmin = data.get("is_superadmin", False)
    apps = await list_incoming_applications(message.from_user.id)
    invited_user_ids = await list_invited_client_user_ids(message.from_user.id)
    if not apps:
        await message.answer("Новых заявок нет.", reply_markup=agent_menu(show_back_to_admin=is_superadmin))
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

    await message.answer("Выберите действие.", reply_markup=agent_menu(show_back_to_admin=is_superadmin))


@router.message(F.text == Btn.IN_PROGRESS)
async def agent_in_progress(message: Message, data: dict) -> None:
    if not await _ensure_agent(message):
        return
    try:
        await message.delete()
    except Exception:
        pass
    is_superadmin = data.get("is_superadmin", False)
    apps = await list_in_progress_applications(message.from_user.id)
    invited_user_ids = await list_invited_client_user_ids(message.from_user.id)
    if not apps:
        await message.answer("Заявок в работе нет.", reply_markup=agent_menu(show_back_to_admin=is_superadmin))
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
    await message.answer("Выберите действие.", reply_markup=agent_menu(show_back_to_admin=is_superadmin))


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
    status_title = "📌" if app.status == ApplicationStatus.new else "🛠"
    text = f"{status_title} Заявка №{app.id}\nСтатус: {app.status.value}\n\n{app.description or 'Без описания'}"
    notes = await list_notes_for_application(callback.from_user.id, app.id, limit=1)
    await callback.message.answer(
        text,
        reply_markup=application_actions_keyboard(
            app.id,
            in_progress=app.status == ApplicationStatus.in_progress,
            has_notes=bool(notes),
        ),
    )
    await callback.answer()


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
        app = await set_application_status(callback.from_user.id, app_id, status=ApplicationStatus.in_progress)
        if app is None:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(f"✅ Заявка №{app_id} взята в работу.")
        status_title = "📌" if app.status == ApplicationStatus.new else "🛠"
        text = f"{status_title} Заявка №{app.id}\nСтатус: {app.status.value}\n\n{app.description or 'Без описания'}"
        notes = await list_notes_for_application(callback.from_user.id, app.id, limit=1)
        await callback.message.answer(
            text,
            reply_markup=application_actions_keyboard(
                app.id,
                in_progress=app.status == ApplicationStatus.in_progress,
                has_notes=bool(notes),
            ),
        )
        await callback.answer()
        return
    if status_s == "done":
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
    try:
        await message.delete()
    except Exception:
        pass
    from bot.handlers.clients import open_clients_menu

    await open_clients_menu(message)


@router.message(F.text == Btn.DASHBOARD)
async def agent_dashboard(message: Message, data: dict) -> None:
    if not await _ensure_agent(message):
        return
    try:
        await message.delete()
    except Exception:
        pass
    is_superadmin = data.get("is_superadmin", False)
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
    await message.answer(txt, reply_markup=agent_menu(show_back_to_admin=is_superadmin))

@router.message(F.text == Btn.REPORTS)
async def agent_reports(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    try:
        await message.delete()
    except Exception:
        pass
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
async def agent_reports_back(callback: CallbackQuery, data: dict) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    is_superadmin = data.get("is_superadmin", False)
    await callback.message.answer("Главное меню", reply_markup=agent_menu(show_back_to_admin=is_superadmin))
    await callback.answer()


@router.message(F.text == Btn.SETTINGS)
async def agent_settings(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer("⚙️ Настройки", reply_markup=_settings_root_keyboard())


@router.callback_query(F.data == "aset:root")
async def settings_root(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    await callback.message.edit_text("⚙️ Настройки", reply_markup=_settings_root_keyboard())
    await callback.answer()


@router.callback_query(F.data == "aset:group:agent")
async def settings_group_agent(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    await callback.message.edit_text("🧑‍💼 Настройки агента", reply_markup=_settings_agent_keyboard())
    await callback.answer()


@router.callback_query(F.data == "aset:group:clients")
async def settings_group_clients(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    await callback.message.edit_text("👥 Работа с клиентами", reply_markup=_settings_clients_keyboard())
    await callback.answer()


@router.callback_query(F.data == "aset:close")
async def settings_close(callback: CallbackQuery) -> None:
    if callback.message is not None:
        try:
            await callback.message.delete()
        except Exception:
            pass
    await callback.answer()


@router.callback_query(F.data == "aset:companies")
async def settings_companies(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    companies = await list_insurance_companies(callback.from_user.id)
    if not companies:
        text = "🏢 Страховые компании\n\nУ вас ещё нет компаний.\nДобавьте первую!"
    else:
        text = "🏢 Страховые компании\n\nВыберите компанию:"
    await callback.message.edit_text(text, reply_markup=_companies_list_kb(companies))
    await callback.answer()


@router.callback_query(F.data == "acomp:add")
async def company_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    await state.clear()
    await state.set_state(CompanySetup.add_company_name)
    try:
        await callback.message.delete()
    except Exception:
        pass
    sent = await callback.message.answer(
        "🏢 Введите название страховой компании:\n\nНапример: БЕЛГОССТРАХ",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="aset:companies")]]
        ),
    )
    await state.update_data(prompt_message_id=sent.message_id)
    await callback.answer()


@router.message(CompanySetup.add_company_name)
async def company_add_save(message: Message, state: FSMContext, is_superadmin: bool = False) -> None:
    if not await _ensure_agent(message):
        return
    data = await state.get_data()
    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)
        except Exception:
            pass
    name = (message.text or "").strip()
    if not name:
        await message.answer("❌ Введите название")
        return
    try:
        await message.delete()
    except Exception:
        pass
    await create_insurance_company(message.from_user.id, name)
    companies = await list_insurance_companies(message.from_user.id)
    await state.clear()
    await message.answer(
        f"✅ Компания «{name}» добавлена!\n\n🏢 Страховые компании:",
        reply_markup=_companies_list_kb(companies),
    )


@router.callback_query(F.data.regexp(r"^acomp:\d+$"))
async def company_detail(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    company_id = int(callback.data.split(":")[1])
    company = await get_insurance_company(callback.from_user.id, company_id)
    if company is None:
        await callback.answer("Компания не найдена", show_alert=True)
        return
    types = await list_insurance_types(callback.from_user.id, company_id)
    await _render_company_detail(callback.message, company.name, company_id, types)
    await callback.answer()


async def _render_company_detail(message: Message, company_name: str, company_id: int, types: list[InsuranceType]) -> None:
    if not types:
        type_text = "   Виды ещё не добавлены."
    else:
        lines = []
        for ins_type in types:
            display = ins_type.custom_name if ins_type.type_key == "other" else INSURANCE_TYPE_KEYS.get(
                ins_type.type_key, ins_type.type_key
            )
            lines.append(f"   • {display}")
        type_text = "\n".join(lines)
    await message.edit_text(
        f"🏢 {company_name}\n\nВиды страхования:\n{type_text}",
        reply_markup=_company_detail_kb(company_id, types),
    )


@router.callback_query(F.data.startswith("acomp:del:"))
async def company_delete_confirm(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    company_id = int(callback.data.split(":")[2])
    company = await get_insurance_company(callback.from_user.id, company_id)
    if company is None:
        await callback.answer("Компания не найдена", show_alert=True)
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"acomp:del_do:{company_id}")],
            [InlineKeyboardButton(text="← Отмена", callback_data=f"acomp:{company_id}")],
        ]
    )
    await callback.message.edit_text(
        f"⚠️ Удалить компанию «{company.name}»?\n\nВсе виды страхования и тарифы будут деактивированы.",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("acomp:del_do:"))
async def company_delete_do(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    company_id = int(callback.data.split(":")[2])
    await deactivate_insurance_company(callback.from_user.id, company_id)
    companies = await list_insurance_companies(callback.from_user.id)
    await callback.message.edit_text(
        "✅ Компания удалена.\n\n🏢 Страховые компании:",
        reply_markup=_companies_list_kb(companies),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("acomp:add_type:"))
async def type_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    company_id = int(callback.data.split(":")[2])
    await state.update_data(company_id=company_id)
    await state.set_state(CompanySetup.add_type_key)
    await callback.message.edit_text("Выберите вид страхования:", reply_markup=_type_keys_kb(company_id))
    await callback.answer()


@router.callback_query(CompanySetup.add_type_key, F.data.startswith("acomp:type_key:"))
async def type_key_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    parts = callback.data.split(":")
    company_id = int(parts[2])
    type_key = parts[3]
    if type_key == "other":
        await state.update_data(company_id=company_id)
        await state.set_state(CompanySetup.add_type_custom)
        try:
            await callback.message.delete()
        except Exception:
            pass
        sent = await callback.message.answer(
            "Введите название вида:\n\nИли нажмите /cancel для отмены.",
            reply_markup=ForceReply(selective=False),
        )
        await state.update_data(prompt_message_id=sent.message_id)
        await callback.answer()
        return
    await create_insurance_type(callback.from_user.id, company_id, type_key)
    await state.clear()
    types = await list_insurance_types(callback.from_user.id, company_id)
    company = await get_insurance_company(callback.from_user.id, company_id)
    display = INSURANCE_TYPE_KEYS.get(type_key, type_key)
    company_name = company.name if company is not None else "Компания"
    await callback.message.edit_text(
        f"✅ Вид «{display}» добавлен!\n\n🏢 {company_name}",
        reply_markup=_company_detail_kb(company_id, types),
    )
    await callback.answer()


@router.message(CompanySetup.add_type_custom)
async def type_custom_save(message: Message, state: FSMContext, is_superadmin: bool = False) -> None:
    if not await _ensure_agent(message):
        return
    state_data = await state.get_data()
    prompt_id = state_data.get("prompt_message_id")
    if prompt_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)
        except Exception:
            pass
    custom_name = (message.text or "").strip()
    if not custom_name:
        await message.answer("❌ Введите название")
        return
    try:
        await message.delete()
    except Exception:
        pass
    company_id = int(state_data["company_id"])
    await create_insurance_type(message.from_user.id, company_id, "other", custom_name)
    await state.clear()
    types = await list_insurance_types(message.from_user.id, company_id)
    company = await get_insurance_company(message.from_user.id, company_id)
    company_name = company.name if company is not None else "Компания"
    await message.answer(
        f"✅ Вид «{custom_name}» добавлен!\n\n🏢 {company_name}",
        reply_markup=_company_detail_kb(company_id, types),
    )


async def _has_other_type_cards(tg_id: int, ins_type: InsuranceType, *, exclude_type_id: int) -> bool:
    all_types = await list_insurance_types(tg_id, active_only=True)
    same_key_types = [t for t in all_types if t.type_key == ins_type.type_key and t.id != exclude_type_id]
    for other_type in same_key_types:
        other_cards = await list_tariff_cards(tg_id, company_id=other_type.company_id)
        if any(c.insurance_type_id == other_type.id for c in other_cards):
            return True
    return False


def _parse_tariff_config(raw_config) -> dict:
    if isinstance(raw_config, dict):
        return raw_config
    if isinstance(raw_config, str):
        try:
            return json.loads(raw_config)
        except Exception:
            return {}
    return {}


@router.callback_query(F.data.startswith("acomp:type:"))
async def insurance_type_detail(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    type_id = int(callback.data.split(":")[2])
    ins_type = await get_insurance_type(callback.from_user.id, type_id)
    if ins_type is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    cards = await list_tariff_cards(callback.from_user.id, company_id=ins_type.company_id)
    existing_card = next((c for c in cards if c.insurance_type_id == type_id), None)
    has_other_cards = await _has_other_type_cards(callback.from_user.id, ins_type, exclude_type_id=type_id)
    display = ins_type.custom_name if ins_type.type_key == "other" else INSURANCE_TYPE_KEYS.get(
        ins_type.type_key, ins_type.type_key
    )
    tariff_status = "✅ Расчёт настроен" if existing_card else "❌ Расчёт не настроен"
    await callback.message.edit_text(
        f"📋 {display}\n\n{tariff_status}\n\n📎 Вложения: нет",
        reply_markup=_insurance_type_detail_kb(type_id, has_card=bool(existing_card), has_other_cards=has_other_cards),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("acomp:type_back:"))
async def type_back(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    type_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id
    ins_type = await get_insurance_type(tg_id, type_id)
    if ins_type is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    company_id = ins_type.company_id
    company = await get_insurance_company(tg_id, company_id)
    if company is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    types = await list_insurance_types(tg_id, company_id)
    await _render_company_detail(callback.message, company.name, company_id, types)
    await callback.answer()


@router.callback_query(F.data.startswith("acomp:del_type:"))
async def insurance_type_delete_confirm(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    type_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id
    ins_type = await get_insurance_type(tg_id, type_id)
    if ins_type is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    display = ins_type.custom_name if ins_type.type_key == "other" else INSURANCE_TYPE_KEYS.get(
        ins_type.type_key, ins_type.type_key
    )
    await callback.message.edit_text(
        f"⚠️ Удалить вид «{display}»?\n\nТарифная карта для этого вида также будет деактивирована.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"acomp:del_type_do:{type_id}")],
                [InlineKeyboardButton(text="← Отмена", callback_data=f"acomp:type:{type_id}")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("acomp:del_type_do:"))
async def insurance_type_delete_do(callback: CallbackQuery) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return
    type_id = int(callback.data.split(":")[-1])
    tg_id = callback.from_user.id
    ins_type = await get_insurance_type(tg_id, type_id)
    if ins_type is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    company_id = ins_type.company_id
    await deactivate_insurance_type(tg_id, type_id)
    company = await get_insurance_company(tg_id, company_id)
    if company is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    types = await list_insurance_types(tg_id, company_id)
    await _render_company_detail(callback.message, company.name, company_id, types)
    await callback.answer()


@router.callback_query(F.data.startswith("acomp:tariff:create:"))
async def tariff_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    type_id = int(callback.data.split(":")[3])
    tg_id = callback.from_user.id
    ins_type = await get_insurance_type(tg_id, type_id)
    if not ins_type:
        await callback.answer("Не найдено")
        return
    if ins_type.type_key != "kasko":
        await callback.answer("Настройка для этого вида будет доступна позже", show_alert=True)
        return

    await state.set_data(
        {
            "tariff_type_id": type_id,
            "company_id": ins_type.company_id,
            "is_editing": False,
            "base_type": None,
            "variants": [],
            "grades": [],
            "coefficients": [],
        }
    )

    has_other_cards = await _has_other_type_cards(tg_id, ins_type, exclude_type_id=type_id)
    type_display = (
        ins_type.custom_name if ins_type.type_key == "other" else INSURANCE_TYPE_KEYS.get(ins_type.type_key, ins_type.type_key)
    )
    if has_other_cards:
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Создать новый расчёт", callback_data="kt:start_fresh")
        builder.button(text="📋 Скопировать из другой компании", callback_data=f"kt:copy_select:{type_id}")
        builder.button(text="❌ Отмена", callback_data=f"acomp:type:{type_id}")
        builder.adjust(1)
        await callback.message.edit_text(
            f"➕ Создание расчёта {type_display}\n\n"
            f"У вас уже есть расчёт {type_display} в другой компании.\n"
            "Хотите скопировать его как основу?",
            reply_markup=builder.as_markup(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "📊 Шаг 1 из 3: Базовый тариф\n\nКак рассчитывается базовый тариф?",
        reply_markup=_kt_base_type_kb(type_id),
    )
    await state.set_state(KaskoTariffSetup.base_type)
    await callback.answer()


@router.callback_query(F.data.startswith("acomp:tariff:edit:"))
async def tariff_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    type_id = int(callback.data.split(":")[3])
    tg_id = callback.from_user.id

    ins_type = await get_insurance_type(tg_id, type_id)
    if not ins_type:
        await callback.answer("Не найдено")
        return

    cards = await list_tariff_cards(tg_id, company_id=ins_type.company_id)
    existing_card = next((c for c in cards if c.insurance_type_id == type_id), None)
    if not existing_card:
        await callback.answer("Расчёт не найден", show_alert=True)
        return

    config = _parse_tariff_config(existing_card.config)
    base = config.get("base", {}) or {}
    min_premium = config.get("min_premium", {}) or {}

    await state.set_data(
        {
            "tariff_type_id": type_id,
            "company_id": ins_type.company_id,
            "is_editing": True,
            "base_type": base.get("type"),
            "base_single_rate": base.get("rate"),
            "variants": base.get("variants", []),
            "grade_by": base.get("grade_by"),
            "grade_label": base.get("grade_label", "Параметр"),
            "grades": base.get("grades", []),
            "coefficients": config.get("coefficients", []),
            "min_premium_enabled": min_premium.get("enabled", False),
            "min_premium_amount": min_premium.get("amount", 0),
            "min_premium_currency": min_premium.get("currency", "BYN"),
        }
    )

    base_type = base.get("type", "—")
    base_display = {
        "single": f"Один тариф: {base.get('rate')}%",
        "variants": f"Вариантов: {len(base.get('variants', []))}",
        "graded": f"Градаций: {len(base.get('grades', []))}",
    }.get(base_type, "—")

    n_coeffs = len(config.get("coefficients", []))
    min_display = (
        f"{min_premium.get('amount', 0)} {min_premium.get('currency', 'BYN')}"
        if min_premium.get("enabled")
        else "нет"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Изменить базовый тариф", callback_data="kt:edit:base")
    builder.button(text="⚙️ Изменить коэффициенты", callback_data="kt:edit:coeffs")
    builder.button(text="💰 Изменить минимум", callback_data="kt:edit:min")
    builder.button(text="🔄 Пересоздать полностью", callback_data=f"kt:recreate:{type_id}")
    builder.button(text="← Назад", callback_data=f"acomp:type:{type_id}")
    builder.adjust(1)

    type_display = (
        ins_type.custom_name if ins_type.type_key == "other" else INSURANCE_TYPE_KEYS.get(ins_type.type_key, ins_type.type_key)
    )
    await callback.message.edit_text(
        f"✏️ Редактирование расчёта {type_display}\n\n"
        f"📊 Базовый тариф: {base_display}\n"
        f"⚙️ Коэффициентов: {n_coeffs}\n"
        f"💰 Минимум: {min_display}\n\n"
        "Что хотите изменить?",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(KaskoTariffSetup.base_type)
    await callback.answer()


@router.callback_query(F.data.startswith("acomp:tariff:copy_from:"))
async def tariff_copy_from_entry(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    type_id = int(callback.data.split(":")[3])
    tg_id = callback.from_user.id
    ins_type = await get_insurance_type(tg_id, type_id)
    if not ins_type:
        await callback.answer("Не найдено", show_alert=True)
        return
    await state.update_data(
        tariff_type_id=type_id,
        company_id=ins_type.company_id,
    )

    all_types = await list_insurance_types(tg_id, active_only=True)
    same_key_types = [t for t in all_types if t.type_key == ins_type.type_key and t.id != type_id]

    sources: list[tuple] = []
    for t in same_key_types:
        cards = await list_tariff_cards(tg_id, company_id=t.company_id)
        card = next((c for c in cards if c.insurance_type_id == t.id), None)
        if card:
            sources.append((t, card))

    if not sources:
        await callback.answer("Нет других расчётов для копирования", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for t, _card in sources:
        company = await get_insurance_company(tg_id, t.company_id)
        company_name = company.name if company else f"Компания {t.company_id}"
        builder.button(text=f"📋 {company_name}", callback_data=f"kt:copy_do:{t.id}")
    builder.button(text="❌ Отмена", callback_data=f"acomp:type:{type_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        "📋 Скопировать расчёт из:\n\nВыберите компанию-источник:",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kt:recreate:"))
async def tariff_recreate(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    type_id = int(callback.data.split(":")[2])
    await state.update_data(
        tariff_type_id=type_id,
        variants=[],
        grades=[],
        coefficients=[],
    )
    await callback.message.edit_text(
        "📊 Шаг 1 из 3: Базовый тариф\n\nКак рассчитывается базовый тариф?",
        reply_markup=_kt_base_type_kb(type_id),
    )
    await state.set_state(KaskoTariffSetup.base_type)
    await callback.answer()


@router.callback_query(F.data == "kt:edit:base")
async def tariff_edit_base(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    type_id = data.get("tariff_type_id")
    if not type_id:
        await callback.answer("Сессия устарела", show_alert=True)
        return
    await callback.message.edit_text(
        "📊 Базовый тариф\n\nВыберите новый тип:",
        reply_markup=_kt_base_type_kb(type_id),
    )
    await state.set_state(KaskoTariffSetup.base_type)
    await callback.answer()


@router.callback_query(F.data == "kt:edit:coeffs")
async def tariff_edit_coeffs(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    coefficients = data.get("coefficients", [])
    builder = InlineKeyboardBuilder()
    for i, coeff in enumerate(coefficients):
        builder.button(text=f"⚙️ {coeff['name']}", callback_data=f"kt:coeff_edit:{i}")
    builder.button(text="➕ Добавить коэффициент", callback_data="kt:coeff:add")
    builder.button(text="💡 Частые коэффициенты", callback_data="kt:coeff:common")
    if coefficients:
        builder.button(text="✅ Готово", callback_data="kt:coeff:done")
    builder.adjust(1)

    text = "⚙️ Коэффициенты:\n\n"
    if coefficients:
        for coeff in coefficients:
            text += (
                f"• {coeff['name']}\n"
                f"  Вопрос: {coeff['question']}\n"
                f"  Да: ×{coeff['yes_value']} | Нет: ×{coeff['no_value']}\n\n"
            )
        text += "Выберите коэффициент для редактирования:"
    else:
        text += "Коэффициентов нет. Добавьте первый."

    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await state.set_state(KaskoTariffSetup.coeff_add_more)
    await callback.answer()


@router.callback_query(F.data == "kt:edit:min")
async def tariff_edit_min(callback: CallbackQuery, state: FSMContext) -> None:
    await _go_to_min_premium(callback, state)


@router.callback_query(F.data == "kt:start_fresh")
async def tariff_start_fresh(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    type_id = data.get("tariff_type_id")
    if not type_id:
        await callback.answer("Сессия устарела", show_alert=True)
        return
    await callback.message.edit_text(
        "📊 Шаг 1 из 3: Базовый тариф\n\nКак рассчитывается базовый тариф?",
        reply_markup=_kt_base_type_kb(type_id),
    )
    await state.set_state(KaskoTariffSetup.base_type)
    await callback.answer()


@router.callback_query(F.data.startswith("kt:copy_select:"))
async def tariff_copy_select(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    type_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id
    ins_type = await get_insurance_type(tg_id, type_id)
    if not ins_type:
        await callback.answer("Не найдено", show_alert=True)
        return

    all_types = await list_insurance_types(tg_id, active_only=True)
    same_key_types = [t for t in all_types if t.type_key == ins_type.type_key and t.id != type_id]

    builder = InlineKeyboardBuilder()
    has_options = False
    for t in same_key_types:
        other_cards = await list_tariff_cards(tg_id, company_id=t.company_id)
        if not any(c.insurance_type_id == t.id for c in other_cards):
            continue
        company = await get_insurance_company(tg_id, t.company_id)
        if company is None:
            continue
        builder.button(text=company.name, callback_data=f"kt:copy_do:{t.id}")
        has_options = True

    if not has_options:
        await callback.answer("Нет доступных расчётов для копирования", show_alert=True)
        return
    builder.button(text="❌ Отмена", callback_data=f"acomp:type:{type_id}")
    builder.adjust(1)
    await callback.message.edit_text(
        "📋 Выберите компанию-источник для копирования расчёта:",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kt:copy_do:"))
async def tariff_copy_do(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    source_type_id = int(callback.data.split(":")[2])
    data = await state.get_data()
    target_type_id = data.get("tariff_type_id")
    if not target_type_id:
        await callback.answer("Сессия устарела", show_alert=True)
        return

    source_type = await get_insurance_type(callback.from_user.id, source_type_id)
    if source_type is None:
        await callback.answer("Источник не найден", show_alert=True)
        return
    source_cards = await list_tariff_cards(callback.from_user.id, company_id=source_type.company_id)
    source_card = next((c for c in source_cards if c.insurance_type_id == source_type_id), None)
    if source_card is None:
        await callback.answer("Расчёт источника не найден", show_alert=True)
        return

    config = _parse_tariff_config(source_card.config)
    base = config.get("base", {}) or {}
    coefficients = config.get("coefficients", []) or []
    min_premium = config.get("min_premium", {}) or {}
    base_type = base.get("type")
    variants = base.get("variants", []) if base_type == "variants" else []
    grades = base.get("grades", []) if base_type == "graded" else []

    await state.update_data(
        base_type=base_type,
        base_single_rate=base.get("rate"),
        variants=variants,
        grade_by=base.get("grade_by"),
        grade_label=base.get("grade_label"),
        grades=grades,
        coefficients=coefficients,
        min_premium_enabled=min_premium.get("enabled", False),
        min_premium_amount=min_premium.get("amount", 0),
        min_premium_currency=min_premium.get("currency", "BYN"),
    )

    company = await get_insurance_company(callback.from_user.id, source_type.company_id)
    company_name = company.name if company else "другой компании"
    if base_type == "single":
        base_display = f"Один тариф: {base.get('rate')}%"
    elif base_type == "variants":
        base_display = f"Вариантов: {len(variants)}"
    elif base_type == "graded":
        base_display = f"Градаций: {len(grades)}"
    else:
        base_display = "—"
    min_display = (
        f"{min_premium.get('amount', 0)} {min_premium.get('currency', 'BYN')}"
        if min_premium.get("enabled")
        else "нет"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Сохранить как есть", callback_data="kt:copy_save")
    builder.button(text="✏️ Редактировать расчёт", callback_data="kt:copy_edit")
    builder.button(text="🔄 Настроить заново", callback_data="kt:start_fresh")
    builder.button(text="❌ Отмена", callback_data=f"acomp:type:{target_type_id}")
    builder.adjust(1)
    await callback.message.edit_text(
        "📋 Скопировано из "
        f"{company_name}\n\n"
        f"📊 Тип: {base_display}\n"
        f"⚙️ Коэффициентов: {len(coefficients)}\n"
        f"💰 Минимум: {min_display}\n\n"
        "Сохранить этот расчёт или настроить заново?",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "kt:copy_edit")
async def tariff_copy_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    base_type = data.get("base_type", "—")
    base_display = {
        "single": f"Один тариф: {data.get('base_single_rate')}%",
        "variants": f"Вариантов: {len(data.get('variants', []))}",
        "graded": f"Градаций: {len(data.get('grades', []))}",
    }.get(base_type, "—")
    n_coeffs = len(data.get("coefficients", []))
    min_enabled = data.get("min_premium_enabled", False)
    min_display = (
        f"{data.get('min_premium_amount')} {data.get('min_premium_currency')}" if min_enabled else "нет"
    )

    await state.update_data(is_editing=True)

    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Изменить базовый тариф", callback_data="kt:edit:base")
    builder.button(text="⚙️ Изменить коэффициенты", callback_data="kt:edit:coeffs")
    builder.button(text="💰 Изменить минимум", callback_data="kt:edit:min")
    builder.button(text="✅ Сохранить без изменений", callback_data="kt:copy_save")
    builder.button(text="← Назад", callback_data="kt:copy_back")
    builder.adjust(1)

    await callback.message.edit_text(
        "✏️ Редактирование скопированного расчёта\n\n"
        f"📊 Базовый тариф: {base_display}\n"
        f"⚙️ Коэффициентов: {n_coeffs}\n"
        f"💰 Минимум: {min_display}\n\n"
        "Что хотите изменить?",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(KaskoTariffSetup.base_type)
    await callback.answer()


@router.callback_query(F.data == "kt:copy_back")
async def tariff_copy_back(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    type_id = data.get("tariff_type_id")

    base_type = data.get("base_type", "—")
    base_display = {
        "single": f"Один тариф: {data.get('base_single_rate')}%",
        "variants": f"Вариантов: {len(data.get('variants', []))}",
        "graded": f"Градаций: {len(data.get('grades', []))}",
    }.get(base_type, "—")
    n_coeffs = len(data.get("coefficients", []))
    min_enabled = data.get("min_premium_enabled", False)
    min_display = (
        f"{data.get('min_premium_amount')} {data.get('min_premium_currency')}" if min_enabled else "нет"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Сохранить как есть", callback_data="kt:copy_save")
    builder.button(text="✏️ Редактировать расчёт", callback_data="kt:copy_edit")
    builder.button(text="🔄 Настроить заново", callback_data="kt:start_fresh")
    builder.button(text="❌ Отмена", callback_data=f"acomp:type:{type_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        "📋 Скопированный расчёт\n\n"
        f"📊 Базовый тариф: {base_display}\n"
        f"⚙️ Коэффициентов: {n_coeffs}\n"
        f"💰 Минимум: {min_display}\n\n"
        "Сохранить или изменить?",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "kt:copy_save")
async def tariff_copy_save(callback: CallbackQuery, state: FSMContext) -> None:
    await _tariff_save_final(callback, state)


@router.callback_query(KaskoTariffSetup.base_type, F.data.startswith("kt:base:"))
async def tariff_base_type_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    base_type = callback.data.split(":")[2]
    await state.update_data(base_type=base_type)
    if base_type == "single":
        await callback.message.edit_text(
            "📊 Базовый тариф\n\nВведите базовый тариф (% от страховой суммы):\n\nНапример: 3.5"
        )
        sent = await callback.message.answer("✏️ Введите число:", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        await state.set_state(KaskoTariffSetup.base_single_rate)
    elif base_type == "variants":
        await state.update_data(variants=[])
        await callback.message.edit_text(
            "📊 Несколько вариантов КАСКО\n\nВведите название варианта 1:\n\nНапример: Базовое КАСКО"
        )
        sent = await callback.message.answer("✏️ Введите название:", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        await state.set_state(KaskoTariffSetup.variant_name)
    elif base_type == "graded":
        await state.update_data(grades=[])
        await callback.message.edit_text(
            "📊 Градация по параметру\n\nПо какому параметру градация?",
            reply_markup=_kt_grade_by_kb(),
        )
        await state.set_state(KaskoTariffSetup.grade_by)
    await callback.answer()


@router.message(KaskoTariffSetup.base_single_rate)
async def tariff_single_rate_save(message: Message, state: FSMContext, is_superadmin: bool = False) -> None:
    await _delete_prompt(message, state)
    try:
        rate = float(message.text.replace(",", "."))  # type: ignore[union-attr]
        if rate <= 0 or rate > 100:
            raise ValueError
    except (ValueError, AttributeError):
        sent = await message.answer("❌ Введите число от 0.01 до 100\nНапример: 3.5", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        return
    await state.update_data(base_single_rate=rate)
    await _go_to_coefficients(message, state)


@router.message(KaskoTariffSetup.variant_name)
async def tariff_variant_name_save(message: Message, state: FSMContext) -> None:
    await _delete_prompt(message, state)
    name = (message.text or "").strip()
    if not name:
        sent = await message.answer("❌ Введите название варианта", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        return
    await state.update_data(current_variant_name=name)
    await message.answer(f"Тариф для «{name}» (%):\n\nНапример: 3.0")
    sent = await message.answer("✏️ Введите %:", reply_markup=ForceReply(selective=False))
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.variant_rate)


@router.message(KaskoTariffSetup.variant_rate)
async def tariff_variant_rate_save(message: Message, state: FSMContext) -> None:
    await _delete_prompt(message, state)
    try:
        rate = float(message.text.replace(",", "."))  # type: ignore[union-attr]
        if rate <= 0 or rate > 100:
            raise ValueError
    except (ValueError, AttributeError):
        sent = await message.answer("❌ Введите число от 0.01 до 100", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        return
    data = await state.get_data()
    variants = data.get("variants", [])
    variants.append({"name": data["current_variant_name"], "rate": rate})
    await state.update_data(variants=variants)
    summary = "\n".join([f"• {v['name']}: {v['rate']}%" for v in variants])
    await message.answer(f"✅ Вариант добавлен!\n\nВарианты:\n{summary}", reply_markup=_kt_variants_summary_kb())
    await state.set_state(KaskoTariffSetup.variant_add_more)


@router.callback_query(KaskoTariffSetup.variant_add_more, F.data == "kt:variant:add")
async def tariff_variant_add_more(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    n = len(data.get("variants", [])) + 1
    await callback.message.edit_text(f"Введите название варианта {n}:")
    sent = await callback.message.answer("✏️ Введите название:", reply_markup=ForceReply(selective=False))
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.variant_name)
    await callback.answer()


@router.callback_query(KaskoTariffSetup.variant_add_more, F.data == "kt:variant:done")
async def tariff_variant_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("variants"):
        await callback.answer("❌ Добавьте хотя бы один вариант", show_alert=True)
        return
    await _go_to_coefficients_cb(callback, state)


@router.callback_query(KaskoTariffSetup.grade_by, F.data.startswith("kt:grade_by:"))
async def tariff_grade_by_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    grade_by = callback.data.split(":")[2]
    if grade_by == "car_age":
        await state.update_data(grade_by="car_age", grade_label="Возраст авто")
        await _ask_grade_item(callback, state)
    else:
        await callback.message.edit_text("Введите название параметра:\n\nНапример: Мощность двигателя")
        sent = await callback.message.answer("✏️ Введите название:", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id, grade_by="custom")
        await state.set_state(KaskoTariffSetup.grade_label)
    await callback.answer()


@router.message(KaskoTariffSetup.grade_label)
async def tariff_grade_label_save(message: Message, state: FSMContext) -> None:
    await _delete_prompt(message, state)
    label = (message.text or "").strip()
    if not label:
        sent = await message.answer("❌ Введите название параметра", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        return
    await state.update_data(grade_label=label)
    await _ask_grade_item_msg(message, state)


async def _ask_grade_item(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    data = await state.get_data()
    label = data.get("grade_label", "параметра")
    n = len(data.get("grades", [])) + 1
    await callback.message.edit_text(f"Градация {n}\n\nНазвание ({label}):\n\nНапример: до 1 года")
    sent = await callback.message.answer("✏️ Введите название:", reply_markup=ForceReply(selective=False))
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.grade_item_label)


async def _ask_grade_item_msg(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    label = data.get("grade_label", "параметра")
    n = len(data.get("grades", [])) + 1
    await message.answer(f"Градация {n}\n\nНазвание ({label}):\n\nНапример: до 1 года")
    sent = await message.answer("✏️ Введите название:", reply_markup=ForceReply(selective=False))
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.grade_item_label)


@router.message(KaskoTariffSetup.grade_item_label)
async def tariff_grade_item_label_save(message: Message, state: FSMContext) -> None:
    await _delete_prompt(message, state)
    label = (message.text or "").strip()
    if not label:
        sent = await message.answer("❌ Введите название градации", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        return
    await state.update_data(current_grade_label=label)
    await message.answer(f"Тариф для «{label}» (%):\n\nНапример: 2.0")
    sent = await message.answer("✏️ Введите %:", reply_markup=ForceReply(selective=False))
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.grade_item_rate)


@router.message(KaskoTariffSetup.grade_item_rate)
async def tariff_grade_item_rate_save(message: Message, state: FSMContext) -> None:
    await _delete_prompt(message, state)
    try:
        rate = float(message.text.replace(",", "."))  # type: ignore[union-attr]
        if rate <= 0 or rate > 100:
            raise ValueError
    except (ValueError, AttributeError):
        sent = await message.answer("❌ Введите число от 0.01 до 100", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        return
    data = await state.get_data()
    grades = data.get("grades", [])
    grades.append({"label": data["current_grade_label"], "rate": rate})
    await state.update_data(grades=grades)
    summary = "\n".join([f"• {g['label']}: {g['rate']}%" for g in grades])
    await message.answer(f"✅ Градация добавлена!\n\nГрадации:\n{summary}", reply_markup=_kt_grades_summary_kb())
    await state.set_state(KaskoTariffSetup.grade_add_more)


@router.callback_query(KaskoTariffSetup.grade_add_more, F.data == "kt:grade:add")
async def tariff_grade_add_more(callback: CallbackQuery, state: FSMContext) -> None:
    await _ask_grade_item(callback, state)
    await callback.answer()


@router.callback_query(KaskoTariffSetup.grade_add_more, F.data == "kt:grade:done")
async def tariff_grade_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("grades"):
        await callback.answer("❌ Добавьте хотя бы одну градацию", show_alert=True)
        return
    await _go_to_coefficients_cb(callback, state)


async def _go_to_coefficients(message: Message, state: FSMContext) -> None:
    await message.answer(
        "📊 Шаг 2 из 3: Коэффициенты\n\n"
        "Корректировочные коэффициенты влияют на итоговый взнос.\n"
        "Каждый станет вопросом клиенту (Да/Нет).",
        reply_markup=_kt_coeff_start_kb(),
    )
    await state.set_state(KaskoTariffSetup.coeff_add_more)


async def _go_to_coefficients_cb(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await callback.message.edit_text(
        "📊 Шаг 2 из 3: Коэффициенты\n\n"
        "Корректировочные коэффициенты влияют на итоговый взнос.\n"
        "Каждый станет вопросом клиенту (Да/Нет).",
        reply_markup=_kt_coeff_start_kb(),
    )
    await state.set_state(KaskoTariffSetup.coeff_add_more)
    await callback.answer()


@router.callback_query(F.data == "kt:coeff:common")
async def tariff_coeff_common_list(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    existing = data.get("coefficients", [])
    existing_names = {c["name"] for c in existing}

    builder = InlineKeyboardBuilder()
    for i, coeff in enumerate(KASKO_COMMON_COEFFICIENTS):
        mark = "✅ " if coeff["name"] in existing_names else ""
        builder.button(text=f"{mark}{coeff['name']}", callback_data=f"kt:coeff:common_pick:{i}")
    builder.button(text="← Назад", callback_data="kt:coeff:back_to_list")
    builder.adjust(1)

    await callback.message.edit_text(
        "💡 Частые коэффициенты КАСКО\n\n"
        "Выберите коэффициент.\n"
        "✅ — уже добавлен.\n\n"
        "После выбора сможете изменить значения.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kt:coeff:common_pick:"))
async def tariff_coeff_common_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        idx = int(callback.data.split(":")[3])
    except (ValueError, IndexError):
        await callback.answer("Не найдено")
        return
    if idx >= len(KASKO_COMMON_COEFFICIENTS):
        await callback.answer("Не найдено")
        return

    coeff = KASKO_COMMON_COEFFICIENTS[idx]
    data = await state.get_data()
    existing = data.get("coefficients", [])
    existing_names = {e["name"] for e in existing}
    already_added = coeff["name"] in existing_names

    builder = InlineKeyboardBuilder()
    if already_added:
        builder.button(text="✅ Уже добавлен — перезаписать?", callback_data=f"kt:coeff:common_add:{idx}")
    else:
        builder.button(text="✅ Добавить как есть", callback_data=f"kt:coeff:common_add:{idx}")
    builder.button(text="✏️ Изменить значения и добавить", callback_data=f"kt:coeff:common_edit:{idx}")
    builder.button(text="← Назад к списку", callback_data="kt:coeff:common")
    builder.adjust(1)

    await callback.message.edit_text(
        f"💡 {coeff['name']}\n\n"
        "Вопрос клиенту:\n"
        f"«{coeff['question']}»\n\n"
        f"Если ДА: ×{coeff['yes_value']}\n"
        f"Если НЕТ: ×{coeff['no_value']}\n\n"
        + ("⚠️ Этот коэффициент уже добавлен.\n" if already_added else ""),
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kt:coeff:common_add:"))
async def tariff_coeff_common_add(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        idx = int(callback.data.split(":")[3])
    except (ValueError, IndexError):
        await callback.answer("Не найдено")
        return
    if idx >= len(KASKO_COMMON_COEFFICIENTS):
        await callback.answer("Не найдено")
        return

    coeff = KASKO_COMMON_COEFFICIENTS[idx]
    data = await state.get_data()
    coefficients = data.get("coefficients", [])

    new_coeff = {
        "id": f"coeff_{uuid.uuid4().hex[:8]}",
        "name": coeff["name"],
        "question": coeff["question"],
        "yes_value": coeff["yes_value"],
        "no_value": coeff["no_value"],
    }

    existing_idx = next((i for i, existing in enumerate(coefficients) if existing["name"] == coeff["name"]), None)
    if existing_idx is not None:
        coefficients[existing_idx] = new_coeff
    else:
        coefficients.append(new_coeff)

    await state.update_data(coefficients=coefficients)
    await callback.answer(f"✅ «{coeff['name']}» добавлен!")
    await tariff_coeff_common_list(callback, state)


@router.callback_query(F.data.startswith("kt:coeff:common_edit:"))
async def tariff_coeff_common_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        idx = int(callback.data.split(":")[3])
    except (ValueError, IndexError):
        await callback.answer("Не найдено")
        return
    if idx >= len(KASKO_COMMON_COEFFICIENTS):
        await callback.answer("Не найдено")
        return

    coeff = KASKO_COMMON_COEFFICIENTS[idx]
    await state.update_data(
        current_coeff_name=coeff["name"],
        current_coeff_question=coeff["question"],
        current_coeff_yes=coeff["yes_value"],
        prefill_common_idx=idx,
    )

    await callback.message.edit_text(
        f"✏️ Редактирование «{coeff['name']}»\n\n"
        f"Название ({coeff['name']}):\n"
        "Введите новое или нажмите 👇\n"
        "чтобы оставить текущее"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text=f"✅ Оставить: {coeff['name']}", callback_data="kt:coeff:common_keep_name")
    builder.adjust(1)
    sent = await callback.message.answer(
        "✏️ Введите название или нажмите кнопку выше:",
        reply_markup=builder.as_markup(),
    )
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.coeff_name)
    await callback.answer()


@router.callback_query(KaskoTariffSetup.coeff_name, F.data == "kt:coeff:common_keep_name")
async def tariff_coeff_common_keep_name(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        try:
            await callback.bot.delete_message(chat_id=callback.message.chat.id, message_id=prompt_id)
        except Exception:
            pass

    c_name = data.get("current_coeff_name", "")
    prefill_idx = data.get("prefill_common_idx")
    coeff = KASKO_COMMON_COEFFICIENTS[prefill_idx] if isinstance(prefill_idx, int) and prefill_idx < len(KASKO_COMMON_COEFFICIENTS) else {}

    await callback.message.answer(f"Вопрос клиенту для «{c_name}»\n\nТекущий: «{coeff.get('question', '')}»")
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Оставить текущий вопрос", callback_data="kt:coeff:common_keep_question")
    builder.adjust(1)
    sent = await callback.message.answer(
        "✏️ Введите вопрос или нажмите кнопку выше:",
        reply_markup=builder.as_markup(),
    )
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.coeff_question)
    await callback.answer()


@router.callback_query(KaskoTariffSetup.coeff_question, F.data == "kt:coeff:common_keep_question")
async def tariff_coeff_common_keep_question(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        try:
            await callback.bot.delete_message(chat_id=callback.message.chat.id, message_id=prompt_id)
        except Exception:
            pass

    prefill_idx = data.get("prefill_common_idx")
    coeff = KASKO_COMMON_COEFFICIENTS[prefill_idx] if isinstance(prefill_idx, int) and prefill_idx < len(KASKO_COMMON_COEFFICIENTS) else {}
    await state.update_data(current_coeff_question=coeff.get("question", ""))

    await callback.message.answer(
        f"Коэффициент если ДА:\n\n"
        f"Текущий: ×{coeff.get('yes_value', 1.0)}\n"
        "Меньше 1 = скидка, больше 1 = надбавка"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text=f"✅ Оставить: ×{coeff.get('yes_value')}", callback_data="kt:coeff:common_keep_yes")
    builder.adjust(1)
    sent = await callback.message.answer(
        "✏️ Введите коэффициент или нажмите кнопку выше:",
        reply_markup=builder.as_markup(),
    )
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.coeff_yes_value)
    await callback.answer()


@router.callback_query(KaskoTariffSetup.coeff_yes_value, F.data == "kt:coeff:common_keep_yes")
async def tariff_coeff_common_keep_yes(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        try:
            await callback.bot.delete_message(chat_id=callback.message.chat.id, message_id=prompt_id)
        except Exception:
            pass

    prefill_idx = data.get("prefill_common_idx")
    coeff = KASKO_COMMON_COEFFICIENTS[prefill_idx] if isinstance(prefill_idx, int) and prefill_idx < len(KASKO_COMMON_COEFFICIENTS) else {}
    await state.update_data(current_coeff_yes=coeff.get("yes_value", 1.0))

    await callback.message.answer(
        f"Коэффициент если НЕТ:\n\n"
        f"Текущий: ×{coeff.get('no_value', 1.0)}"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text=f"✅ Оставить: ×{coeff.get('no_value')}", callback_data="kt:coeff:common_keep_no")
    builder.adjust(1)
    sent = await callback.message.answer(
        "✏️ Введите коэффициент или нажмите кнопку выше:",
        reply_markup=builder.as_markup(),
    )
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.coeff_no_value)
    await callback.answer()


@router.callback_query(KaskoTariffSetup.coeff_no_value, F.data == "kt:coeff:common_keep_no")
async def tariff_coeff_common_keep_no(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        try:
            await callback.bot.delete_message(chat_id=callback.message.chat.id, message_id=prompt_id)
        except Exception:
            pass

    prefill_idx = data.get("prefill_common_idx")
    coeff = KASKO_COMMON_COEFFICIENTS[prefill_idx] if isinstance(prefill_idx, int) and prefill_idx < len(KASKO_COMMON_COEFFICIENTS) else {}
    no_value = coeff.get("no_value", 1.0)
    await state.update_data(current_coeff_no=no_value)

    data = await state.get_data()
    coefficients = data.get("coefficients", [])
    new_coeff = {
        "id": f"coeff_{uuid.uuid4().hex[:8]}",
        "name": data["current_coeff_name"],
        "question": data["current_coeff_question"],
        "yes_value": data["current_coeff_yes"],
        "no_value": no_value,
    }

    existing_idx = next((i for i, existing in enumerate(coefficients) if existing["name"] == new_coeff["name"]), None)
    if existing_idx is not None:
        coefficients[existing_idx] = new_coeff
    else:
        coefficients.append(new_coeff)

    await state.update_data(coefficients=coefficients, prefill_common_idx=None)

    lines = [f"• {item['name']}: Да×{item['yes_value']} / Нет×{item['no_value']}" for item in coefficients]
    await callback.message.answer(
        "✅ Коэффициент добавлен!\n\n"
        "Коэффициенты:\n"
        + "\n".join(lines),
        reply_markup=_kt_coeff_summary_kb(),
    )
    await state.set_state(KaskoTariffSetup.coeff_add_more)
    await callback.answer()


@router.callback_query(F.data == "kt:coeff:back_to_list")
async def tariff_coeff_back_to_list(callback: CallbackQuery, state: FSMContext) -> None:
    await tariff_edit_coeffs(callback, state)


@router.callback_query(KaskoTariffSetup.coeff_add_more, F.data == "kt:coeff:add")
async def tariff_coeff_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await callback.message.edit_text("⚙️ Новый коэффициент\n\nВведите название:\n\nНапример: Противоугонная система")
    sent = await callback.message.answer("✏️ Введите название:", reply_markup=ForceReply(selective=False))
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.coeff_name)
    await callback.answer()


@router.message(KaskoTariffSetup.coeff_name)
async def tariff_coeff_name_save(message: Message, state: FSMContext) -> None:
    await _delete_prompt(message, state)
    name = (message.text or "").strip()
    if not name:
        sent = await message.answer("❌ Введите название", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        return
    await state.update_data(current_coeff_name=name)
    await message.answer(f"Вопрос клиенту для «{name}»:\n\nНапример: Есть противоугонная система?")
    sent = await message.answer("✏️ Введите вопрос:", reply_markup=ForceReply(selective=False))
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.coeff_question)


@router.message(KaskoTariffSetup.coeff_question)
async def tariff_coeff_question_save(message: Message, state: FSMContext) -> None:
    await _delete_prompt(message, state)
    question = (message.text or "").strip()
    if not question:
        sent = await message.answer("❌ Введите вопрос", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        return
    await state.update_data(current_coeff_question=question)
    await message.answer(
        "Коэффициент если ответ ДА:\n\n"
        "Меньше 1 = скидка (например 0.95)\n"
        "Больше 1 = надбавка (например 1.1)\n"
        "1.0 = без изменений"
    )
    sent = await message.answer("✏️ Введите коэффициент:", reply_markup=ForceReply(selective=False))
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.coeff_yes_value)


@router.message(KaskoTariffSetup.coeff_yes_value)
async def tariff_coeff_yes_save(message: Message, state: FSMContext) -> None:
    await _delete_prompt(message, state)
    try:
        val = float(message.text.replace(",", "."))  # type: ignore[union-attr]
        if val <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        sent = await message.answer("❌ Введите число больше 0\nНапример: 0.95", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        return
    await state.update_data(current_coeff_yes=val)
    await message.answer("Коэффициент если ответ НЕТ:\n\nОбычно 1.0 (без изменений)")
    sent = await message.answer("✏️ Введите коэффициент:", reply_markup=ForceReply(selective=False))
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.coeff_no_value)


@router.message(KaskoTariffSetup.coeff_no_value)
async def tariff_coeff_no_save(message: Message, state: FSMContext) -> None:
    await _delete_prompt(message, state)
    try:
        val = float(message.text.replace(",", "."))  # type: ignore[union-attr]
        if val <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        sent = await message.answer("❌ Введите число больше 0\nНапример: 1.0", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        return
    data = await state.get_data()
    coefficients = data.get("coefficients", [])
    coefficients.append(
        {
            "id": f"coeff_{uuid.uuid4().hex[:8]}",
            "name": data["current_coeff_name"],
            "question": data["current_coeff_question"],
            "yes_value": data["current_coeff_yes"],
            "no_value": val,
        }
    )
    await state.update_data(coefficients=coefficients)
    summary = "\n".join([f"• {c['name']}: Да×{c['yes_value']} / Нет×{c['no_value']}" for c in coefficients])
    await message.answer(f"✅ Коэффициент добавлен!\n\nКоэффициенты:\n{summary}", reply_markup=_kt_coeff_summary_kb())
    await state.set_state(KaskoTariffSetup.coeff_add_more)


@router.callback_query(KaskoTariffSetup.coeff_add_more, F.data == "kt:coeff:done")
async def tariff_coeff_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("is_editing"):
        await _tariff_save_final(callback, state)
        return
    await _go_to_min_premium(callback, state)


@router.callback_query(F.data.startswith("kt:coeff_edit:"))
async def tariff_coeff_edit_card(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    idx = int(callback.data.split(":")[2])
    data = await state.get_data()
    coefficients = data.get("coefficients", [])
    if idx >= len(coefficients):
        await callback.answer("Не найдено")
        return

    coeff = coefficients[idx]
    await state.update_data(editing_coeff_idx=idx)

    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Название", callback_data=f"kt:cedit:name:{idx}")
    builder.button(text="✏️ Вопрос клиенту", callback_data=f"kt:cedit:question:{idx}")
    builder.button(text="✏️ Значение если ДА", callback_data=f"kt:cedit:yes:{idx}")
    builder.button(text="✏️ Значение если НЕТ", callback_data=f"kt:cedit:no:{idx}")
    builder.button(text="🗑 Удалить коэффициент", callback_data=f"kt:cedit:delete:{idx}")
    builder.button(text="← Назад к списку", callback_data="kt:edit:coeffs")
    builder.adjust(1)

    await callback.message.edit_text(
        f"⚙️ {coeff['name']}\n\n"
        f"Вопрос: {coeff['question']}\n"
        f"Если ДА: ×{coeff['yes_value']}\n"
        f"Если НЕТ: ×{coeff['no_value']}\n\n"
        "Что редактируем?",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kt:cedit:delete:"))
async def tariff_coeff_delete(callback: CallbackQuery, state: FSMContext) -> None:
    idx = int(callback.data.split(":")[3])
    data = await state.get_data()
    coefficients = data.get("coefficients", [])
    if idx < len(coefficients):
        deleted = coefficients.pop(idx)
        await state.update_data(coefficients=coefficients)
        await callback.answer(f"✅ «{deleted['name']}» удалён")
    await tariff_edit_coeffs(callback, state)


@router.callback_query(F.data.startswith("kt:cedit:name:"))
async def tariff_coeff_edit_name_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    idx = int(callback.data.split(":")[3])
    await state.update_data(editing_coeff_idx=idx)
    data = await state.get_data()
    coefficients = data.get("coefficients", [])
    if idx >= len(coefficients):
        await callback.answer("Не найдено")
        return
    coeff = coefficients[idx]
    await callback.message.edit_text(
        f"✏️ Название коэффициента\n\n"
        f"Текущее: {coeff['name']}\n\n"
        "Введите новое название:"
    )
    sent = await callback.message.answer("✏️ Введите название:", reply_markup=ForceReply(selective=False))
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.coeff_edit_name)
    await callback.answer()


@router.message(KaskoTariffSetup.coeff_edit_name)
async def tariff_coeff_edit_name_save(message: Message, state: FSMContext) -> None:
    await _delete_prompt(message, state)
    name = (message.text or "").strip()
    if not name:
        sent = await message.answer("❌ Введите название", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        return
    data = await state.get_data()
    idx = data.get("editing_coeff_idx")
    coefficients = data.get("coefficients", [])
    if idx is None or idx >= len(coefficients):
        await message.answer("❌ Коэффициент не найден")
        return
    coefficients[idx]["name"] = name
    await state.update_data(coefficients=coefficients)
    await message.answer(
        f"✅ Название обновлено: {name}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="← К коэффициенту", callback_data=f"kt:coeff_edit:{idx}")]
            ]
        ),
    )
    await state.set_state(KaskoTariffSetup.coeff_add_more)


@router.callback_query(F.data.startswith("kt:cedit:question:"))
async def tariff_coeff_edit_question_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    idx = int(callback.data.split(":")[3])
    await state.update_data(editing_coeff_idx=idx)
    data = await state.get_data()
    coefficients = data.get("coefficients", [])
    if idx >= len(coefficients):
        await callback.answer("Не найдено")
        return
    coeff = coefficients[idx]
    await callback.message.edit_text(
        f"✏️ Вопрос клиенту\n\n"
        f"Текущий: {coeff['question']}\n\n"
        "Введите новый вопрос:"
    )
    sent = await callback.message.answer("✏️ Введите вопрос:", reply_markup=ForceReply(selective=False))
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.coeff_edit_question)
    await callback.answer()


@router.message(KaskoTariffSetup.coeff_edit_question)
async def tariff_coeff_edit_question_save(message: Message, state: FSMContext) -> None:
    await _delete_prompt(message, state)
    question = (message.text or "").strip()
    if not question:
        sent = await message.answer("❌ Введите вопрос", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        return
    data = await state.get_data()
    idx = data.get("editing_coeff_idx")
    coefficients = data.get("coefficients", [])
    if idx is None or idx >= len(coefficients):
        await message.answer("❌ Коэффициент не найден")
        return
    coefficients[idx]["question"] = question
    await state.update_data(coefficients=coefficients)
    await message.answer(
        "✅ Вопрос обновлён",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="← К коэффициенту", callback_data=f"kt:coeff_edit:{idx}")]
            ]
        ),
    )
    await state.set_state(KaskoTariffSetup.coeff_add_more)


@router.callback_query(F.data.startswith("kt:cedit:yes:"))
async def tariff_coeff_edit_yes_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    idx = int(callback.data.split(":")[3])
    await state.update_data(editing_coeff_idx=idx)
    data = await state.get_data()
    coefficients = data.get("coefficients", [])
    if idx >= len(coefficients):
        await callback.answer("Не найдено")
        return
    coeff = coefficients[idx]
    await callback.message.edit_text(
        f"✏️ Значение если ДА\n\n"
        f"Текущее: ×{coeff['yes_value']}\n\n"
        "Введите новое значение:\n"
        "Меньше 1 = скидка (0.95)\n"
        "Больше 1 = надбавка (1.1)\n"
        "1.0 = без изменений"
    )
    sent = await callback.message.answer("✏️ Введите коэффициент:", reply_markup=ForceReply(selective=False))
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.coeff_edit_yes)
    await callback.answer()


@router.message(KaskoTariffSetup.coeff_edit_yes)
async def tariff_coeff_edit_yes_save(message: Message, state: FSMContext) -> None:
    await _delete_prompt(message, state)
    try:
        val = float(message.text.replace(",", "."))  # type: ignore[union-attr]
        if val <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        sent = await message.answer("❌ Введите число больше 0\nНапример: 0.95", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        return
    data = await state.get_data()
    idx = data.get("editing_coeff_idx")
    coefficients = data.get("coefficients", [])
    if idx is None or idx >= len(coefficients):
        await message.answer("❌ Коэффициент не найден")
        return
    coefficients[idx]["yes_value"] = val
    await state.update_data(coefficients=coefficients)
    await message.answer(
        f"✅ Значение ДА обновлено: ×{val}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="← К коэффициенту", callback_data=f"kt:coeff_edit:{idx}")]
            ]
        ),
    )
    await state.set_state(KaskoTariffSetup.coeff_add_more)


@router.callback_query(F.data.startswith("kt:cedit:no:"))
async def tariff_coeff_edit_no_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    idx = int(callback.data.split(":")[3])
    await state.update_data(editing_coeff_idx=idx)
    data = await state.get_data()
    coefficients = data.get("coefficients", [])
    if idx >= len(coefficients):
        await callback.answer("Не найдено")
        return
    coeff = coefficients[idx]
    await callback.message.edit_text(
        f"✏️ Значение если НЕТ\n\n"
        f"Текущее: ×{coeff['no_value']}\n\n"
        "Введите новое значение:"
    )
    sent = await callback.message.answer("✏️ Введите коэффициент:", reply_markup=ForceReply(selective=False))
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.coeff_edit_no)
    await callback.answer()


@router.message(KaskoTariffSetup.coeff_edit_no)
async def tariff_coeff_edit_no_save(message: Message, state: FSMContext) -> None:
    await _delete_prompt(message, state)
    try:
        val = float(message.text.replace(",", "."))  # type: ignore[union-attr]
        if val <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        sent = await message.answer("❌ Введите число больше 0\nНапример: 1.0", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        return
    data = await state.get_data()
    idx = data.get("editing_coeff_idx")
    coefficients = data.get("coefficients", [])
    if idx is None or idx >= len(coefficients):
        await message.answer("❌ Коэффициент не найден")
        return
    coefficients[idx]["no_value"] = val
    await state.update_data(coefficients=coefficients)
    await message.answer(
        f"✅ Значение НЕТ обновлено: ×{val}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="← К коэффициенту", callback_data=f"kt:coeff_edit:{idx}")]
            ]
        ),
    )
    await state.set_state(KaskoTariffSetup.coeff_add_more)


@router.callback_query(KaskoTariffSetup.coeff_add_more, F.data == "kt:coeff:skip")
async def tariff_coeff_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await _go_to_min_premium(callback, state)


async def _go_to_min_premium(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await callback.message.edit_text(
        "📊 Шаг 3 из 3: Минимальный взнос\n\n"
        "Есть ли минимальный страховой взнос?\n\n"
        "Например: не менее 400 USD",
        reply_markup=_kt_min_ask_kb(),
    )
    await state.set_state(KaskoTariffSetup.min_premium_ask)
    await callback.answer()


@router.callback_query(KaskoTariffSetup.min_premium_ask, F.data == "kt:min:no")
async def tariff_min_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(min_premium_enabled=False, min_premium_amount=0, min_premium_currency="BYN")
    await _tariff_save_final(callback, state)


@router.callback_query(KaskoTariffSetup.min_premium_ask, F.data == "kt:min:yes")
async def tariff_min_yes(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await callback.message.edit_text("Введите минимальную сумму:\n\nНапример: 400")
    sent = await callback.message.answer("✏️ Введите сумму:", reply_markup=ForceReply(selective=False))
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(KaskoTariffSetup.min_premium_amount)
    await callback.answer()


@router.message(KaskoTariffSetup.min_premium_amount)
async def tariff_min_amount_save(message: Message, state: FSMContext) -> None:
    await _delete_prompt(message, state)
    try:
        amount = float(message.text.replace(",", "."))  # type: ignore[union-attr]
        if amount <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        sent = await message.answer("❌ Введите число больше 0\nНапример: 400", reply_markup=ForceReply(selective=False))
        await state.update_data(prompt_message_id=sent.message_id)
        return
    await state.update_data(min_premium_enabled=True, min_premium_amount=amount)
    await message.answer("Валюта минимального взноса:", reply_markup=_kt_currency_kb())
    await state.set_state(KaskoTariffSetup.min_premium_currency)


@router.callback_query(KaskoTariffSetup.min_premium_currency, F.data.startswith("kt:min:cur:"))
async def tariff_min_currency_pick(callback: CallbackQuery, state: FSMContext) -> None:
    currency = callback.data.split(":")[3]
    await state.update_data(min_premium_currency=currency)
    await _tariff_save_final(callback, state)


async def _tariff_save_final(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    tg_id = callback.from_user.id
    type_id = data["tariff_type_id"]
    base = {"type": data["base_type"]}
    if data["base_type"] == "single":
        base["rate"] = data["base_single_rate"]
    elif data["base_type"] == "variants":
        base["variants"] = data["variants"]
    elif data["base_type"] == "graded":
        base["grade_by"] = data["grade_by"]
        base["grade_label"] = data.get("grade_label", "Параметр")
        base["grades"] = data["grades"]
    config = {
        "version": 1,
        "insurance_type": "kasko",
        "base": base,
        "coefficients": data.get("coefficients", []),
        "min_premium": {
            "enabled": data.get("min_premium_enabled", False),
            "amount": data.get("min_premium_amount", 0),
            "currency": data.get("min_premium_currency", "BYN"),
        },
    }
    await upsert_tariff_card(
        agent_tg_id=tg_id,
        insurance_type_id=type_id,
        card_type="kasko_parametric",
        config=config,
        company_id=data.get("company_id"),
    )
    await state.clear()

    base_display = {
        "single": f"Один тариф: {data.get('base_single_rate')}%",
        "variants": f"Вариантов: {len(data.get('variants', []))}",
        "graded": f"Градаций: {len(data.get('grades', []))}",
    }.get(data["base_type"], "—")
    min_display = (
        f"{data.get('min_premium_amount')} {data.get('min_premium_currency')}" if data.get("min_premium_enabled") else "нет"
    )
    n_coeffs = len(data.get("coefficients", []))
    await callback.message.edit_text(
        "✅ Расчёт сохранён!\n\n"
        "🚗 КАСКО\n\n"
        f"📊 Базовый тариф: {base_display}\n"
        f"⚙️ Коэффициентов: {n_coeffs}\n"
        f"💰 Минимум: {min_display}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="← К виду страхования", callback_data=f"acomp:type:{type_id}")]
            ]
        ),
    )
    await callback.answer()


def _calc_currency_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for cur in ("BYN", "USD", "EUR", "RUB", "CNY"):
        builder.button(text=cur, callback_data=f"calc:cur:{cur}")
    builder.adjust(3, 2)
    return builder.as_markup()


def _calc_value_kb(currency: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for val in (10000, 20000, 30000, 40000, 50000, 60000):
        builder.button(text=f"{val:,} {currency}", callback_data=f"calc:val:{val}")
    builder.adjust(3, 3)
    return builder.as_markup()


def _calc_agent_result_kb(type_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Новый расчёт", callback_data="calc:new")
    builder.button(text="💾 Сохранить в заметки", callback_data="calc:save_note")
    builder.button(text="📤 Отправить клиенту", callback_data="calc:send_client")
    builder.button(text="← Назад к виду", callback_data=f"acomp:type:{type_id}")
    builder.adjust(1)
    return builder.as_markup()


def _calc_client_result_kb(has_more: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_more:
        builder.button(text="➡️ Рассчитать вариант 2", callback_data="calc:client:next_company")
    builder.button(text="📝 Отправить заявку", callback_data="calc:client:apply")
    builder.button(text="🔄 Новый расчёт", callback_data="calc:new")
    builder.button(text="← Назад", callback_data="calc:back")
    builder.adjust(1)
    return builder.as_markup()


def _format_calc_result(
    result: dict,
    ins_value: float,
    currency: str,
    type_display: str,
    variant_num: int | None = None,
) -> str:
    lines = [f"🧮 Расчёт {type_display}"]
    if variant_num is not None:
        lines.append(f"Вариант {variant_num}")
    lines.append("")
    lines.append(f"💰 Страховая сумма: {_format_amount(ins_value, currency)}")
    lines.append(f"📊 Базовый тариф: {result.get('base_rate', 0)}%")
    lines.append(f"   ({result.get('base_label', '—')})")
    applied = result.get("applied_coeffs", []) or []
    if applied:
        lines.append("")
        lines.append("⚙️ Применённые коэффициенты:")
        for coeff in applied:
            lines.append(f"• {coeff.get('name')}: {coeff.get('answer')} → ×{coeff.get('value')}")
    lines.append("")
    lines.append(f"💵 Страховой взнос: {_format_amount(result.get('premium', 0.0), currency)}")
    if result.get("min_applied"):
        lines.append(
            f"⚠️ Применён минимальный взнос: {result.get('min_premium')} {result.get('min_currency')}"
        )
    elif result.get("min_premium") and result.get("min_currency") and currency != result.get("min_currency"):
        lines.append(
            f"ℹ️ Минимальный взнос агента: {result.get('min_premium')} {result.get('min_currency')}"
        )
    return "\n".join(lines)


@router.callback_query(F.data.startswith("acomp:calc:"))
async def agent_calc_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    type_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id
    ins_type = await get_insurance_type(tg_id, type_id)
    if not ins_type:
        await callback.answer("Не найдено")
        return
    cards = await list_tariff_cards(tg_id, company_id=ins_type.company_id)
    card = next((c for c in cards if c.insurance_type_id == type_id), None)
    if not card:
        await callback.answer("❌ Расчёт не настроен", show_alert=True)
        return

    config = _parse_tariff_config(card.config)
    type_display = (
        ins_type.custom_name if ins_type.type_key == "other" else INSURANCE_TYPE_KEYS.get(ins_type.type_key, ins_type.type_key)
    )
    await state.set_data(
        {
            "calc_type_id": type_id,
            "calc_company_id": ins_type.company_id,
            "calc_config": config,
            "calc_type_display": type_display,
            "calc_coeff_answers": {},
            "calc_coeff_idx": 0,
            "calc_is_agent": True,
        }
    )
    await callback.message.edit_text(
        f"🧮 Проверка расчёта: {type_display}\n\nШаг 1: Выберите валюту страховой суммы:",
        reply_markup=_calc_currency_kb(),
    )
    await state.set_state(InsuranceCalc.currency)
    await callback.answer()


@router.callback_query(InsuranceCalc.currency, F.data.startswith("calc:cur:"))
async def calc_currency_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    currency = callback.data.split(":")[2]
    await state.update_data(calc_currency=currency)
    type_display = (await state.get_data()).get("calc_type_display", "страховки")
    await callback.message.edit_text(
        f"🧮 Расчёт: {type_display}\n\nШаг 2: Введите страховую сумму или выберите из списка ({currency}):",
        reply_markup=_calc_value_kb(currency),
    )
    sent = await callback.message.answer("✏️ Или введите свою сумму:", reply_markup=ForceReply(selective=False))
    await state.update_data(prompt_message_id=sent.message_id)
    await state.set_state(InsuranceCalc.ins_value)
    await callback.answer()


@router.callback_query(InsuranceCalc.ins_value, F.data.startswith("calc:val:"))
async def calc_value_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    value = float(callback.data.split(":")[2])
    data = await state.get_data()
    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        try:
            await callback.bot.delete_message(chat_id=callback.message.chat.id, message_id=prompt_id)
        except Exception:
            pass
    await state.update_data(calc_ins_value=value, prompt_message_id=None)
    await _calc_ask_next_coeff(callback.message, state, edit=True)
    await callback.answer()


@router.message(InsuranceCalc.ins_value)
async def calc_value_input(message: Message, state: FSMContext) -> None:
    await _delete_prompt(message, state)
    try:
        value = float((message.text or "").replace(",", ".").replace(" ", "").replace("\xa0", ""))
        if value <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        sent = await message.answer(
            "❌ Введите число больше 0\nНапример: 35000",
            reply_markup=ForceReply(selective=False),
        )
        await state.update_data(prompt_message_id=sent.message_id)
        return
    await state.update_data(calc_ins_value=value)
    await _calc_ask_next_coeff(message, state, edit=False)


async def _calc_ask_next_coeff(msg_obj, state: FSMContext, edit: bool = False) -> None:
    data = await state.get_data()
    config = data["calc_config"]
    coefficients = config.get("coefficients", [])
    idx = data.get("calc_coeff_idx", 0)
    if idx >= len(coefficients):
        await _calc_show_result(msg_obj, state, edit)
        return
    coeff = coefficients[idx]
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data="calc:coeff:yes")
    builder.button(text="❌ Нет", callback_data="calc:coeff:no")
    builder.adjust(2)
    text = f"❓ {coeff['question']}"
    if edit and hasattr(msg_obj, "edit_text"):
        await msg_obj.edit_text(text, reply_markup=builder.as_markup())
    else:
        await msg_obj.answer(text, reply_markup=builder.as_markup())
    await state.set_state(InsuranceCalc.coefficients)


@router.callback_query(InsuranceCalc.coefficients, F.data.in_({"calc:coeff:yes", "calc:coeff:no"}))
async def calc_coeff_answer(callback: CallbackQuery, state: FSMContext) -> None:
    answer = callback.data == "calc:coeff:yes"
    data = await state.get_data()
    coefficients = data.get("calc_config", {}).get("coefficients", [])
    idx = data.get("calc_coeff_idx", 0)
    if idx < len(coefficients):
        coeff = coefficients[idx]
        answers = data.get("calc_coeff_answers", {})
        answers[coeff["id"]] = answer
        await state.update_data(calc_coeff_answers=answers, calc_coeff_idx=idx + 1)
    await _calc_ask_next_coeff(callback.message, state, edit=True)
    await callback.answer()


async def _calc_show_result(msg_obj, state: FSMContext, edit: bool = False) -> None:
    data = await state.get_data()
    config = data["calc_config"]
    ins_value = data["calc_ins_value"]
    currency = data["calc_currency"]
    answers = data.get("calc_coeff_answers", {})
    is_agent = data.get("calc_is_agent", False)
    type_display = data.get("calc_type_display", "страховки")
    type_id = data.get("calc_type_id")
    result = _calculate_premium(config, ins_value, currency, answers)
    await state.update_data(calc_result=result)

    configs = data.get("ccalc_configs", [])
    current_idx = data.get("ccalc_current_idx", 0)
    has_more = not is_agent and current_idx + 1 < len(configs)
    if not is_agent:
        results = data.get("ccalc_results", [])
        results.append(result)
        await state.update_data(ccalc_results=results)
    variant_num = current_idx + 1 if (not is_agent and len(configs) > 1) else None
    text = _format_calc_result(result, ins_value, currency, type_display, variant_num)
    kb = _calc_agent_result_kb(type_id) if is_agent else _calc_client_result_kb(has_more)
    if edit and hasattr(msg_obj, "edit_text"):
        await msg_obj.edit_text(text, reply_markup=kb)
    else:
        await msg_obj.answer(text, reply_markup=kb)
    await state.set_state(InsuranceCalc.result_shown)


@router.callback_query(InsuranceCalc.result_shown, F.data == "calc:new")
async def calc_new(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    type_display = data.get("calc_type_display", "страховки")
    await state.update_data(
        calc_currency=None,
        calc_ins_value=None,
        calc_coeff_answers={},
        calc_coeff_idx=0,
        calc_result=None,
        ccalc_results=[],
        ccalc_current_idx=0,
        prompt_message_id=None,
    )
    await callback.message.edit_text(
        f"🧮 Новый расчёт: {type_display}\n\nШаг 1: Выберите валюту:",
        reply_markup=_calc_currency_kb(),
    )
    await state.set_state(InsuranceCalc.currency)
    await callback.answer()


@router.callback_query(InsuranceCalc.result_shown, F.data == "calc:back")
async def calc_back(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    type_id = data.get("calc_type_id")
    is_agent = data.get("calc_is_agent", False)
    await state.clear()
    if is_agent and type_id:
        await callback.message.edit_text(
            "← Возврат к виду страхования",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="← К виду", callback_data=f"acomp:type:{type_id}")]]
            ),
        )
    else:
        await callback.message.delete()
    await callback.answer()


@router.callback_query(InsuranceCalc.result_shown, F.data == "calc:save_note")
async def calc_save_note(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("💾 Будет доступно в следующей версии", show_alert=True)


@router.callback_query(InsuranceCalc.result_shown, F.data == "calc:send_client")
async def calc_send_client(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("📤 Будет доступно в следующей версии", show_alert=True)


@router.callback_query(InsuranceCalc.result_shown, F.data == "calc:client:next_company")
async def calc_client_next_company(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    configs = data.get("ccalc_configs", [])
    current_idx = data.get("ccalc_current_idx", 0)
    next_idx = current_idx + 1
    if next_idx >= len(configs):
        await _show_all_results(callback, state)
        return
    next_config = configs[next_idx]
    type_display = data.get("calc_type_display", "страховки")
    await state.update_data(
        ccalc_current_idx=next_idx,
        calc_config=next_config["config"],
        calc_coeff_answers={},
        calc_coeff_idx=0,
    )
    await callback.message.edit_text(
        f"🧮 Расчёт: {type_display}\nВариант {next_idx + 1} из {len(configs)}\n\nШаг 1: Выберите валюту:",
        reply_markup=_calc_currency_kb(),
    )
    await state.set_state(InsuranceCalc.currency)
    await callback.answer()


async def _show_all_results(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    results = data.get("ccalc_results", [])
    ins_value = data.get("calc_ins_value", 0)
    currency = data.get("calc_currency", "BYN")
    type_display = data.get("calc_type_display", "страховки")
    lines = [
        f"🧮 Все варианты расчёта: {type_display}\n",
        f"💰 Страховая сумма: {_format_amount(ins_value, currency)}\n",
    ]
    for i, result in enumerate(results, 1):
        lines.append(f"━━ Вариант {i} ━━")
        lines.append(f"💵 Взнос: {_format_amount(result['premium'], currency)}")
        if result.get("min_applied"):
            lines.append(f"⚠️ Применён минимум: {result['min_premium']} {result['min_currency']}")
        lines.append("")
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Отправить заявку", callback_data="calc:client:apply")
    builder.button(text="🔄 Новый расчёт", callback_data="calc:new")
    builder.adjust(1)
    await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup())
    await state.set_state(InsuranceCalc.result_shown)
    await callback.answer()


@router.callback_query(InsuranceCalc.result_shown, F.data == "calc:client:apply")
async def calc_client_apply(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    from bot.db.repo import create_application_for_client  # local import to avoid large top-level dependency additions

    data = await state.get_data()
    tg_id = callback.from_user.id
    results = data.get("ccalc_results", [])
    ins_value = data.get("calc_ins_value", 0)
    currency = data.get("calc_currency", "BYN")
    type_display = data.get("calc_type_display", "страховки")

    if results:
        premiums = "\n".join([f"Вариант {i}: {_format_amount(r['premium'], currency)}" for i, r in enumerate(results, 1)])
    else:
        result = data.get("calc_result", {})
        premiums = _format_amount(result.get("premium", 0), currency)

    description = (
        f"Расчёт: {type_display}\n"
        f"Страховая сумма: {_format_amount(ins_value, currency)}\n"
        f"Результат:\n{premiums}"
    )
    await create_application_for_client(tg_id, description=description)
    await state.clear()
    await callback.message.edit_text(
        "✅ Заявка отправлена агенту!\n\nАгент свяжется с вами в ближайшее время.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="client:menu")]]
        ),
    )
    await callback.answer()

async def _delete_prompt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass


@router.callback_query(F.data == "kt:cancel")
async def tariff_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    type_id = data.get("tariff_type_id")
    await state.clear()
    if type_id:
        await callback.message.edit_text(
            "❌ Создание отменено.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="← Назад", callback_data=f"acomp:type:{type_id}")]
                ]
            ),
        )
    else:
        await callback.message.delete()
    await callback.answer()


@router.callback_query(F.data == "acomp:soon")
async def company_soon(callback: CallbackQuery) -> None:
    await callback.answer("🔒 Будет доступно в следующей версии", show_alert=True)


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
    sent = await callback.message.answer(
        "Введите новый пароль агента (минимум 6 символов):\n\nИли нажмите /cancel для отмены.",
        reply_markup=ForceReply(selective=False),
    )
    await state.update_data(prompt_message_id=sent.message_id)
    await callback.answer()


@router.callback_query(F.data == "aset:profile_name")
async def settings_profile_name(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    current_name = await get_user_display_name(callback.from_user.id)
    await state.clear()
    await state.set_state(AgentProfileSetup.name)
    current_line = f"Текущее имя: {current_name}\n" if current_name else ""
    sent = await callback.message.answer(
        f"{current_line}Введите имя агента (как будет видно клиентам):\n\nИли нажмите /cancel для отмены.",
        reply_markup=ForceReply(selective=False),
    )
    await state.update_data(prompt_message_id=sent.message_id)
    await callback.answer()


@router.message(AgentProfileSetup.name)
async def settings_profile_name_save(message: Message, state: FSMContext) -> None:
    try:
        state_data = await state.get_data()
        prompt_id = state_data.get("prompt_message_id")
        if prompt_id:
            try:
                await message.bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)
            except Exception:
                pass
        try:
            await message.delete()
        except Exception:
            pass
        name = (message.text or "").strip()
        if not name:
            sent = await message.answer("❌ Имя не может быть пустым.\nВведите имя агента:")
            await state.update_data(prompt_message_id=sent.message_id)
            return
        await set_agent_display_name(message.from_user.id, name)
        await state.clear()
        await message.answer(
            f"✅ Имя обновлено!\n\n"
            f"Новое имя: {name}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="← Настройки агента", callback_data="aset:group:agent")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="aset:close")],
                ]
            ),
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}\n\nПопробуйте ещё раз или нажмите /start")
        await state.clear()


@router.callback_query(F.data == "aset:contacts")
async def settings_contacts_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    phones, email, telegram = await get_agent_contacts(callback.from_user.id)
    await state.clear()
    await state.set_state(AgentProfileSetup.contacts_phones)
    phones_line = ", ".join(phones) if phones else "—"
    telegram_line = f"@{telegram}" if telegram else "—"
    sent = await callback.message.answer(
        f"Текущие телефоны: {phones_line}\n"
        f"Текущий Telegram: {telegram_line}\n"
        f"Текущий email: {email or '—'}\n\n"
        "Введите телефоны через запятую (или 'нет'):\n\n"
        "Или нажмите /cancel для отмены.",
        reply_markup=ForceReply(selective=False),
    )
    await state.update_data(prompt_message_id=sent.message_id)
    await callback.answer()


@router.message(AgentProfileSetup.contacts_phones)
async def settings_contacts_phones(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    state_data = await state.get_data()
    prompt_id = state_data.get("prompt_message_id")
    if prompt_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)
        except Exception:
            pass
    txt = (message.text or "").strip()
    phones = [] if txt.lower() == "нет" else [x.strip() for x in txt.split(",") if x.strip()]
    await state.update_data(agent_contacts_phones=phones)
    await state.set_state(AgentProfileSetup.contacts_telegram)
    await message.answer("Введите Telegram аккаунт агента (username, можно с @) или 'нет':")


@router.message(AgentProfileSetup.contacts_telegram)
async def settings_contacts_telegram(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    txt = (message.text or "").strip()
    telegram = None if txt.lower() == "нет" else txt
    await state.update_data(agent_contacts_telegram=telegram)
    await state.set_state(AgentProfileSetup.contacts_email)
    await message.answer("Введите email агента (или 'нет'):")


@router.message(AgentProfileSetup.contacts_email)
async def settings_contacts_email(message: Message, state: FSMContext, data: dict) -> None:
    if not await _ensure_agent(message):
        return
    is_superadmin = data.get("is_superadmin", False)
    txt = (message.text or "").strip()
    email = None if txt.lower() == "нет" else txt
    data = await state.get_data()
    phones = list(data.get("agent_contacts_phones") or [])
    telegram = data.get("agent_contacts_telegram")
    ok = await set_agent_contacts(message.from_user.id, phones, email, telegram)
    await state.clear()
    if not ok:
        await message.answer("Не удалось сохранить контакты.", reply_markup=agent_menu(show_back_to_admin=is_superadmin))
        return
    await message.answer(
        "✅ Контакты агента сохранены.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="← Настройки агента", callback_data="aset:group:agent")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="aset:close")],
            ]
        ),
    )


@router.callback_query(F.data == "aset:broadcast")
async def settings_broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await state.clear()
    await state.set_state(AgentBroadcast.text)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="aset:broadcast:cancel")]]
    )
    await callback.message.answer(
        "Введите текст сообщения для рассылки всем привязанным клиентам:",
        reply_markup=kb,
    )
    await callback.answer()


@router.message(AgentBroadcast.text)
async def settings_broadcast_send(message: Message, state: FSMContext, data: dict) -> None:
    if not await _ensure_agent(message):
        return
    is_superadmin = data.get("is_superadmin", False)
    txt = (message.text or "").strip()
    if len(txt) < 2:
        await message.answer("Текст слишком короткий.")
        return
    targets = await list_bound_client_tg_for_agent(message.from_user.id)
    if not targets:
        await state.clear()
        await message.answer("Нет привязанных клиентов для рассылки.", reply_markup=agent_menu(show_back_to_admin=is_superadmin))
        return
    await state.update_data(agent_broadcast_text=txt)
    await state.set_state(AgentBroadcast.confirm)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить", callback_data="aset:broadcast:send")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="aset:broadcast:cancel")],
        ]
    )
    await message.answer(
        f"Предпросмотр рассылки ({len(targets)} клиентов):\n\n📣 Сообщение от агента:\n{txt}",
        reply_markup=kb,
    )


@router.callback_query(F.data == "aset:broadcast:cancel")
async def settings_broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message is not None:
        await callback.message.edit_text(
            "👥 Работа с клиентами",
            reply_markup=_settings_clients_keyboard(),
        )
    await callback.answer("Отменено")


@router.callback_query(F.data == "aset:broadcast:send")
async def settings_broadcast_confirm_send(callback: CallbackQuery, state: FSMContext, data: dict) -> None:
    if callback.message is None:
        await callback.answer()
        return
    is_superadmin = data.get("is_superadmin", False)
    if not await _ensure_agent_tg(callback.from_user.id):
        await state.clear()
        await callback.answer("Недоступно", show_alert=True)
        return
    data = await state.get_data()
    txt = str(data.get("agent_broadcast_text") or "").strip()
    if len(txt) < 2:
        await state.clear()
        await callback.message.answer("Текст рассылки потерян. Запустите заново.", reply_markup=agent_menu(show_back_to_admin=is_superadmin))
        await callback.answer()
        return
    targets = await list_bound_client_tg_for_agent(callback.from_user.id)
    if not targets:
        await state.clear()
        await callback.message.answer("Нет привязанных клиентов для рассылки.", reply_markup=agent_menu(show_back_to_admin=is_superadmin))
        await callback.answer()
        return
    sent = 0
    for tg_id, _name in targets:
        try:
            await callback.bot.send_message(tg_id, f"📣 Сообщение от агента:\n{txt}")
            sent += 1
        except Exception:
            pass
    await state.clear()
    await callback.message.answer(f"✅ Рассылка отправлена: {sent} из {len(targets)}.", reply_markup=agent_menu(show_back_to_admin=is_superadmin))
    await callback.answer("Отправлено")


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
async def settings_back(callback: CallbackQuery, data: dict) -> None:
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    is_superadmin = data.get("is_superadmin", False)
    await callback.message.answer("Выберите действие.", reply_markup=agent_menu(show_back_to_admin=is_superadmin))
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
    state_data = await state.get_data()
    prompt_id = state_data.get("prompt_message_id")
    if prompt_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)
        except Exception:
            pass
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
    await message.answer(
        "✅ Пароль агента сохранён.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="← Настройки агента", callback_data="aset:group:agent")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="aset:close")],
            ]
        ),
    )


