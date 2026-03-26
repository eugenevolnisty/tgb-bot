from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.db.models import QuoteType, UserRole
from bot.db.repo import create_generic_quote, get_or_create_user
from bot.keyboards import apply_quote_keyboard, client_menu, to_main_menu_keyboard
from bot.services.accident_travel import AccidentTravelInput, calculate_accident_travel
from bot.services.expeditor import parse_plan_choice
from bot.services.generic_calc import GenericInput, calculate_generic

router = Router()


class GenericCalc(StatesGroup):
    other_kind = State()
    full_name = State()
    contact = State()
    subject = State()
    value = State()
    comment = State()
    accident_days = State()
    accident_age = State()
    accident_variants = State()
    accident_sum_a = State()
    accident_sum_b = State()
    accident_territory = State()
    accident_sport = State()
    accident_count = State()
    accident_repeat = State()
    accident_epolicy = State()
    expeditor_plan = State()
    cmr_cargo_type = State()
    cmr_limit = State()
    cmr_auto_count = State()


_CMR_CARGO_TITLES: dict[str, str] = {
    "ordinary": "обычные грузы",
    "refrigerated_dangerous": "рефрижераторные, опасные грузы",
    "open_platform": "перевозки на открытых платформах",
}

_CMR_LIMITS: tuple[int, int, int] = (100000, 150000, 250000)

# Base premium for 1 vehicle (EUR) from tariff table.
_CMR_PREMIUM_PER_AUTO: dict[tuple[str, int], float] = {
    ("ordinary", 100000): 190.3,
    ("ordinary", 150000): 208.0,
    ("ordinary", 250000): 270.4,
    ("refrigerated_dangerous", 100000): 237.6,
    ("refrigerated_dangerous", 150000): 259.2,
    ("refrigerated_dangerous", 250000): 338.0,
    ("open_platform", 100000): 227.7,
    ("open_platform", 150000): 249.6,
    ("open_platform", 250000): 322.4,
}


def _cmr_cargo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Обычные грузы", callback_data="cmr:cargo:ordinary")],
            [
                InlineKeyboardButton(
                    text="Рефрижераторные / опасные",
                    callback_data="cmr:cargo:refrigerated_dangerous",
                )
            ],
            [InlineKeyboardButton(text="Открытые платформы", callback_data="cmr:cargo:open_platform")],
        ]
    )


def _cmr_limit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="100 000 EUR", callback_data="cmr:limit:100000")],
            [InlineKeyboardButton(text="150 000 EUR", callback_data="cmr:limit:150000")],
            [InlineKeyboardButton(text="250 000 EUR", callback_data="cmr:limit:250000")],
        ]
    )


def _expeditor_plan_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Базовый", callback_data="expplan:basic")],
            [InlineKeyboardButton(text="Стандарт", callback_data="expplan:standard")],
            [InlineKeyboardButton(text="Премиум", callback_data="expplan:premium")],
            [InlineKeyboardButton(text="Максимальный", callback_data="expplan:max")],
        ]
    )


def _accident_variant_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Вариант A", callback_data="acc:variant:A")],
            [InlineKeyboardButton(text="Вариант B", callback_data="acc:variant:B")],
        ]
    )


def _accident_sum_a_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="30 000", callback_data="acc:suma:30000")],
            [InlineKeyboardButton(text="50 000", callback_data="acc:suma:50000")],
            [InlineKeyboardButton(text="70 000", callback_data="acc:suma:70000")],
            [InlineKeyboardButton(text="100 000", callback_data="acc:suma:100000")],
        ]
    )


def _accident_sum_b_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="5 000", callback_data="acc:sumb:5000")],
            [InlineKeyboardButton(text="10 000", callback_data="acc:sumb:10000")],
            [InlineKeyboardButton(text="20 000", callback_data="acc:sumb:20000")],
        ]
    )


def _accident_ab5_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1", callback_data="acc:ab5:1")],
            [InlineKeyboardButton(text="2", callback_data="acc:ab5:2")],
            [InlineKeyboardButton(text="3", callback_data="acc:ab5:3")],
            [InlineKeyboardButton(text="4", callback_data="acc:ab5:4")],
            [InlineKeyboardButton(text="5", callback_data="acc:ab5:5")],
        ]
    )


def _yes_no_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=f"{prefix}:yes"),
                InlineKeyboardButton(text="Нет", callback_data=f"{prefix}:no"),
            ]
        ]
    )


async def _ensure_client(message: Message) -> bool:
    user = await get_or_create_user(message.from_user.id)
    return user.role == UserRole.client


async def _ensure_client_tg(tg_id: int) -> bool:
    user = await get_or_create_user(tg_id)
    return user.role == UserRole.client


def _kind_title(kind: str, other_kind: str | None = None) -> str:
    m = {
        "cargo": "📦 Грузы",
        "accident": "✈️ Страховка за границу",
        "expeditor": "🚛 Ответственность экспедитора",
        "cmr": "🚚 CMR",
        "dms": "🩺 ДМС",
        "other": "✍️ Другой вид",
    }
    t = m.get(kind, "✍️ Другой вид")
    if kind == "other" and other_kind:
        return f"{t} ({other_kind})"
    return t


async def start_generic_from_callback(kind: str, callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    if not await _ensure_client_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    await state.clear()
    await state.update_data(kind=kind)
    if kind == "other":
        await state.set_state(GenericCalc.other_kind)
        await callback.message.answer("✍️ Укажите вид страхования (текстом). Можно “отмена”.", reply_markup=to_main_menu_keyboard())
        return
    await state.set_state(GenericCalc.full_name)
    await callback.message.answer(
        f"{_kind_title(kind)} — расчёт.\nКак вас зовут? Можно “отмена”.",
        reply_markup=to_main_menu_keyboard(),
    )


@router.message(GenericCalc.other_kind)
async def step_other_kind(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 2:
        await message.answer("Укажи вид чуть подробнее.")
        return
    await state.update_data(other_kind=text)
    await state.set_state(GenericCalc.full_name)
    kind = (await state.get_data()).get("kind", "other")
    await message.answer(f"{_kind_title(str(kind), text)} — расчёт.\nКак вас зовут?")


@router.message(GenericCalc.full_name)
async def step_full_name(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 2:
        await message.answer("Напиши имя, например: Евгений.")
        return
    await state.update_data(full_name=text)
    await state.set_state(GenericCalc.contact)
    await message.answer("📞 Как с вами связаться? (телефон / Telegram / e-mail)")


@router.message(GenericCalc.contact)
async def step_contact(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("Укажи контакт (например: +375... или @username).")
        return
    await state.update_data(contact=text)
    data = await state.get_data()
    if str(data.get("kind")) == "accident":
        await state.set_state(GenericCalc.accident_days)
        await message.answer("Сколько дней поездка? (1-365)")
        return
    if str(data.get("kind")) == "expeditor":
        await state.set_state(GenericCalc.expeditor_plan)
        await message.answer(
            "Выберите пакет страхования ответственности экспедитора:\n"
            "1) Базовый — лимит на 1 случай 50 000, агрегатный лимит 250 000, франшиза 500\n"
            "2) Стандарт — лимит на 1 случай 100 000, агрегатный лимит 500 000, франшиза 1 000\n"
            "3) Премиум — лимит на 1 случай 250 000, агрегатный лимит 750 000, франшиза 2 000\n"
            "4) Максимальный — лимит на 1 случай 500 000, агрегатный лимит 750 000, франшиза 1 500\n"
            "Нажмите кнопку ниже или напишите номер 1-4 / название пакета.",
            reply_markup=_expeditor_plan_keyboard(),
        )
        return
    if str(data.get("kind")) == "cmr":
        await state.set_state(GenericCalc.cmr_cargo_type)
        await message.answer(
            "🚚 CMR: выберите тип груза.",
            reply_markup=_cmr_cargo_keyboard(),
        )
        return
    await state.set_state(GenericCalc.subject)
    await message.answer("Что вы хотите застраховать? (текстом)")


@router.callback_query(GenericCalc.cmr_cargo_type, F.data.startswith("cmr:cargo:"))
async def cmr_pick_cargo(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_client_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    cargo = callback.data.split(":")[-1]
    if cargo not in _CMR_CARGO_TITLES:
        await callback.answer("Некорректный тип груза", show_alert=True)
        return
    await state.update_data(cmr_cargo_type=cargo)
    await state.set_state(GenericCalc.cmr_limit)
    await callback.message.answer(
        "Выберите лимит ответственности:",
        reply_markup=_cmr_limit_keyboard(),
    )
    await callback.answer()


@router.callback_query(GenericCalc.cmr_limit, F.data.startswith("cmr:limit:"))
async def cmr_pick_limit(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_client_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        limit = int(callback.data.split(":")[-1])
    except ValueError:
        await callback.answer("Некорректный лимит", show_alert=True)
        return
    if limit not in _CMR_LIMITS:
        await callback.answer("Некорректный лимит", show_alert=True)
        return
    await state.update_data(cmr_limit=limit)
    await state.set_state(GenericCalc.cmr_auto_count)
    await callback.message.answer("Сколько автомобилей? Введите число (1, 2, 3 ...).")
    await callback.answer()


@router.message(GenericCalc.cmr_auto_count)
async def cmr_auto_count(message: Message, state: FSMContext) -> None:
    try:
        auto_count = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введите количество автомобилей числом, например: 3")
        return
    if auto_count < 1:
        await message.answer("Количество автомобилей должно быть от 1.")
        return

    data = await state.get_data()
    cargo = str(data.get("cmr_cargo_type", ""))
    limit = int(data.get("cmr_limit", 0))
    per_auto = _CMR_PREMIUM_PER_AUTO.get((cargo, limit))
    if per_auto is None:
        await message.answer("Не удалось рассчитать CMR. Попробуйте начать заново.")
        await state.clear()
        await message.answer("Главное меню", reply_markup=client_menu())
        return

    premium = per_auto * auto_count
    full_name = str(data.get("full_name", ""))
    contact = str(data.get("contact", ""))
    cargo_title = _CMR_CARGO_TITLES[cargo]

    payload = {
        "full_name": full_name,
        "contact": contact,
        "cargo_type": cargo,
        "cargo_title": cargo_title,
        "liability_limit_eur": limit,
        "vehicles_count": auto_count,
        "premium_per_vehicle_eur": per_auto,
    }
    saved = await create_generic_quote(
        message.from_user.id,
        quote_type=QuoteType.cmr,
        input_payload=payload,
        premium_byn=premium,
        currency="EUR",
    )

    lines = [
        "🚚 CMR — расчёт",
        f"👤 Имя: {full_name}",
        f"📞 Контакт: {contact}",
        f"📦 Тип груза: {cargo_title}",
        f"🛡 Лимит ответственности: {limit:,} EUR".replace(",", " "),
        f"🚛 Кол-во автомобилей: {auto_count}",
        f"💰 Тариф за 1 авто: {per_auto:.2f} EUR",
        f"💰 Итог: {premium:.2f} EUR",
        "",
        f"Расчёт сохранён (№{saved.id}). Можешь оформить заявку.",
    ]

    await state.clear()
    await message.answer("\n".join(lines), reply_markup=apply_quote_keyboard(saved.id))
    await message.answer("Главное меню", reply_markup=client_menu())


@router.message(GenericCalc.expeditor_plan)
async def expeditor_plan(message: Message, state: FSMContext) -> None:
    plan = parse_plan_choice(message.text or "")
    if plan is None:
        await message.answer("Не понял пакет. Введите 1, 2, 3 или 4 (или название пакета).")
        return
    data = await state.get_data()
    full_name = str(data.get("full_name", ""))
    contact = str(data.get("contact", ""))

    payload = {
        "full_name": full_name,
        "contact": contact,
        "plan_key": plan.key,
        "plan_title": plan.title,
        "per_case_limit": plan.per_case_limit,
        "aggregate_limit": plan.aggregate_limit,
        "franchise": plan.franchise,
        "territory": "Все страны мира",
        "shipments": "Неограниченное количество проэкспедированных перевозок",
    }
    saved = await create_generic_quote(
        message.from_user.id,
        quote_type=QuoteType.expeditor,
        input_payload=payload,
        premium_byn=plan.premium,
        currency="USD/EUR",
    )
    lines = [
        "🚛 Ответственность экспедитора",
        f"👤 Имя: {full_name}",
        f"📞 Контакт: {contact}",
        f"📦 Пакет: {plan.title}",
        f"• Лимит на 1 случай: {plan.per_case_limit:,} USD/EUR".replace(",", " "),
        f"• Агрегатный лимит: {plan.aggregate_limit:,} USD/EUR".replace(",", " "),
        f"• Франшиза: {plan.franchise:,} USD/EUR".replace(",", " "),
        "• Территория: все страны мира",
        "• Кол-во проэкспедированных перевозок: неограниченно",
        "",
        f"💰 Стоимость: {plan.premium:.2f} USD/EUR",
        f"Расчёт сохранён (№{saved.id}). Можешь оформить заявку.",
    ]
    await state.clear()
    await message.answer("\n".join(lines), reply_markup=apply_quote_keyboard(saved.id))
    await message.answer("Главное меню", reply_markup=client_menu())


@router.callback_query(GenericCalc.expeditor_plan, F.data.startswith("expplan:"))
async def expeditor_plan_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_client_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return

    plan_key = callback.data.split(":")[-1]
    choice_map = {"basic": "1", "standard": "2", "premium": "3", "max": "4"}
    raw_choice = choice_map.get(plan_key)
    if raw_choice is None:
        await callback.answer("Некорректный пакет", show_alert=True)
        return

    plan = parse_plan_choice(raw_choice)
    if plan is None:
        await callback.answer("Не удалось прочитать пакет", show_alert=True)
        return

    data = await state.get_data()
    full_name = str(data.get("full_name", ""))
    contact = str(data.get("contact", ""))

    payload = {
        "full_name": full_name,
        "contact": contact,
        "plan_key": plan.key,
        "plan_title": plan.title,
        "per_case_limit": plan.per_case_limit,
        "aggregate_limit": plan.aggregate_limit,
        "franchise": plan.franchise,
        "territory": "Все страны мира",
        "shipments": "Неограниченное количество проэкспедированных перевозок",
    }
    saved = await create_generic_quote(
        callback.from_user.id,
        quote_type=QuoteType.expeditor,
        input_payload=payload,
        premium_byn=plan.premium,
        currency="USD/EUR",
    )
    lines = [
        "🚛 Ответственность экспедитора",
        f"👤 Имя: {full_name}",
        f"📞 Контакт: {contact}",
        f"📦 Пакет: {plan.title}",
        f"• Лимит на 1 случай: {plan.per_case_limit:,} USD/EUR".replace(",", " "),
        f"• Агрегатный лимит: {plan.aggregate_limit:,} USD/EUR".replace(",", " "),
        f"• Франшиза: {plan.franchise:,} USD/EUR".replace(",", " "),
        "• Территория: все страны мира",
        "• Кол-во проэкспедированных перевозок: неограниченно",
        "",
        f"💰 Стоимость: {plan.premium:.2f} USD/EUR",
        f"Расчёт сохранён (№{saved.id}). Можешь оформить заявку.",
    ]
    await state.clear()
    await callback.message.answer("\n".join(lines), reply_markup=apply_quote_keyboard(saved.id))
    await callback.message.answer("Главное меню", reply_markup=client_menu())
    await callback.answer()


def _yes_no(text: str) -> bool | None:
    t = text.strip().lower()
    if t in {"да", "yes", "y", "1"}:
        return True
    if t in {"нет", "no", "n", "0"}:
        return False
    return None


@router.message(GenericCalc.accident_days)
async def accident_days(message: Message, state: FSMContext) -> None:
    try:
        days = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введи число дней, например: 14")
        return
    if days < 1 or days > 365:
        await message.answer("Срок поездки должен быть от 1 до 365 дней.")
        return
    await state.update_data(acc_days=days)
    await state.set_state(GenericCalc.accident_age)
    await message.answer("Возраст застрахованного (полных лет)?")


@router.message(GenericCalc.accident_age)
async def accident_age(message: Message, state: FSMContext) -> None:
    try:
        age = int((message.text or "").strip())
    except ValueError:
        await message.answer("Возраст должен быть числом, например: 32")
        return
    if age < 0 or age > 120:
        await message.answer("Введи возраст в диапазоне 0-120.")
        return
    await state.update_data(acc_age=age)
    await state.set_state(GenericCalc.accident_variants)
    await message.answer(
        "Какой вариант страхования?\n"
        "A (30 000, 50 000, 70 000 или 100 000)\n"
        "или B (5 000, 10 000 или 20 000).",
        reply_markup=_accident_variant_keyboard(),
    )


@router.callback_query(GenericCalc.accident_variants, F.data.startswith("acc:variant:"))
async def accident_variants_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    mark = callback.data.split(":")[-1].upper()
    if mark == "A":
        va, vb = True, False
    elif mark == "B":
        va, vb = False, True
    else:
        await callback.answer("Нужен вариант A или B", show_alert=True)
        return
    await state.update_data(acc_variant_a=va, acc_variant_b=vb)
    if va:
        await state.set_state(GenericCalc.accident_sum_a)
        await callback.message.answer(
            "Страховая сумма по A: 30000 / 50000 / 70000 / 100000",
            reply_markup=_accident_sum_a_keyboard(),
        )
    else:
        await state.set_state(GenericCalc.accident_sum_b)
        await callback.message.answer(
            "Страховая сумма по B: 5000 / 10000 / 20000",
            reply_markup=_accident_sum_b_keyboard(),
        )
    await callback.answer()


@router.message(GenericCalc.accident_variants)
async def accident_variants(message: Message, state: FSMContext) -> None:
    t = (message.text or "").strip().upper().replace("+", "")
    if t in {"A", "А"}:
        va, vb = True, False
    elif t in {"B", "В"}:
        va, vb = False, True
    else:
        await message.answer("Напиши только A или B.")
        return
    await state.update_data(acc_variant_a=va, acc_variant_b=vb)
    if va:
        await state.set_state(GenericCalc.accident_sum_a)
        await message.answer(
            "Страховая сумма по A: 30000 / 50000 / 70000 / 100000",
            reply_markup=_accident_sum_a_keyboard(),
        )
    else:
        await state.set_state(GenericCalc.accident_sum_b)
        await message.answer(
            "Страховая сумма по B: 5000 / 10000 / 20000",
            reply_markup=_accident_sum_b_keyboard(),
        )


@router.callback_query(GenericCalc.accident_sum_a, F.data.startswith("acc:suma:"))
async def accident_sum_a_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        val = int(callback.data.split(":")[-1])
    except ValueError:
        await callback.answer("Некорректная сумма", show_alert=True)
        return
    if val not in {30000, 50000, 70000, 100000}:
        await callback.answer("Допустимо: 30000 / 50000 / 70000 / 100000", show_alert=True)
        return
    await state.update_data(acc_sum_a=val)
    await state.set_state(GenericCalc.accident_territory)
    await callback.message.answer(
        "Выбери вариант территории AB5 (1-5):\n"
        "1 — любые страны мира, кроме Индии, Индонезии, Таиланда, Израиля, США, Канады\n"
        "2 — все страны мира, кроме Индии, Индонезии, Таиланда, Израиля, США, Канады\n"
        "3 — Индия, Индонезия, Таиланд\n"
        "4 — Израиль, США, Канада\n"
        "5 — все страны мира (по всему миру)",
        reply_markup=_accident_ab5_keyboard(),
    )
    await callback.answer()


@router.message(GenericCalc.accident_sum_a)
async def accident_sum_a(message: Message, state: FSMContext) -> None:
    try:
        val = int((message.text or "").strip().replace(" ", ""))
    except ValueError:
        await message.answer("Введи сумму числом: 30000 / 50000 / 70000 / 100000")
        return
    if val not in {30000, 50000, 70000, 100000}:
        await message.answer("Допустимо: 30000 / 50000 / 70000 / 100000")
        return
    await state.update_data(acc_sum_a=val)
    await state.set_state(GenericCalc.accident_territory)
    await message.answer(
        "Выбери вариант территории AB5 (1-5):\n"
        "1 — любые страны мира, кроме Индии, Индонезии, Таиланда, Израиля, США, Канады\n"
        "2 — все страны мира, кроме Индии, Индонезии, Таиланда, Израиля, США, Канады\n"
        "3 — Индия, Индонезия, Таиланд\n"
        "4 — Израиль, США, Канада\n"
        "5 — все страны мира (по всему миру)",
        reply_markup=_accident_ab5_keyboard(),
    )


@router.callback_query(GenericCalc.accident_sum_b, F.data.startswith("acc:sumb:"))
async def accident_sum_b_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        val = int(callback.data.split(":")[-1])
    except ValueError:
        await callback.answer("Некорректная сумма", show_alert=True)
        return
    if val not in {5000, 10000, 20000}:
        await callback.answer("Допустимо: 5000 / 10000 / 20000", show_alert=True)
        return
    await state.update_data(acc_sum_b=val)
    await state.set_state(GenericCalc.accident_territory)
    await callback.message.answer(
        "Выбери вариант территории AB5 (1-5):\n"
        "1 — любые страны мира, кроме Индии, Индонезии, Таиланда, Израиля, США, Канады\n"
        "2 — все страны мира, кроме Индии, Индонезии, Таиланда, Израиля, США, Канады\n"
        "3 — Индия, Индонезия, Таиланд\n"
        "4 — Израиль, США, Канада\n"
        "5 — все страны мира (по всему миру)",
        reply_markup=_accident_ab5_keyboard(),
    )
    await callback.answer()


@router.message(GenericCalc.accident_sum_b)
async def accident_sum_b(message: Message, state: FSMContext) -> None:
    try:
        val = int((message.text or "").strip().replace(" ", ""))
    except ValueError:
        await message.answer("Введи сумму числом: 5000 / 10000 / 20000")
        return
    if val not in {5000, 10000, 20000}:
        await message.answer("Допустимо: 5000 / 10000 / 20000")
        return
    await state.update_data(acc_sum_b=val)
    await state.set_state(GenericCalc.accident_territory)
    await message.answer(
        "Выбери вариант территории AB5 (1-5):\n"
        "1 — любые страны мира, кроме Индии, Индонезии, Таиланда, Израиля, США, Канады\n"
        "2 — все страны мира, кроме Индии, Индонезии, Таиланда, Израиля, США, Канады\n"
        "3 — Индия, Индонезия, Таиланд\n"
        "4 — Израиль, США, Канада\n"
        "5 — все страны мира (по всему миру)",
        reply_markup=_accident_ab5_keyboard(),
    )


@router.callback_query(GenericCalc.accident_territory, F.data.startswith("acc:ab5:"))
async def accident_territory_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        opt = int(callback.data.split(":")[-1])
    except ValueError:
        await callback.answer("Нужен номер 1-5", show_alert=True)
        return
    if opt not in {1, 2, 3, 4, 5}:
        await callback.answer("Допустимо только 1-5", show_alert=True)
        return
    await state.update_data(acc_ab5=opt)
    await state.set_state(GenericCalc.accident_sport)
    await callback.message.answer("Есть спорт/тренировки (A2)?", reply_markup=_yes_no_keyboard("acc:sport"))
    await callback.answer()


@router.message(GenericCalc.accident_territory)
async def accident_territory(message: Message, state: FSMContext) -> None:
    try:
        opt = int((message.text or "").strip())
    except ValueError:
        await message.answer(
            "Нужен номер варианта AB5: 1, 2, 3, 4 или 5.\n"
            "1 — кроме Индии/Индонезии/Таиланда/Израиля/США/Канады; "
            "3 — Индия/Индонезия/Таиланд; 4 — Израиль/США/Канада; 5 — весь мир."
        )
        return
    if opt not in {1, 2, 3, 4, 5}:
        await message.answer("Допустимо только 1-5. Выбери один номер варианта AB5.")
        return
    await state.update_data(acc_ab5=opt)
    await state.set_state(GenericCalc.accident_sport)
    await message.answer("Есть спорт/тренировки (A2)?", reply_markup=_yes_no_keyboard("acc:sport"))


@router.callback_query(GenericCalc.accident_sport, F.data.startswith("acc:sport:"))
async def accident_sport_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    mark = callback.data.split(":")[-1]
    if mark == "yes":
        b = True
    elif mark == "no":
        b = False
    else:
        await callback.answer("Ответь: да или нет", show_alert=True)
        return
    await state.update_data(acc_sport=b)
    await state.set_state(GenericCalc.accident_count)
    await callback.message.answer("Сколько человек страхуется по договору? (число)")
    await callback.answer()


@router.message(GenericCalc.accident_sport)
async def accident_sport(message: Message, state: FSMContext) -> None:
    b = _yes_no(message.text or "")
    if b is None:
        await message.answer("Ответь: да или нет.")
        return
    await state.update_data(acc_sport=b)
    await state.set_state(GenericCalc.accident_count)
    await message.answer("Сколько человек страхуется по договору? (число)")


@router.message(GenericCalc.accident_count)
async def accident_count(message: Message, state: FSMContext) -> None:
    try:
        cnt = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введи количество числом, например: 1")
        return
    if cnt < 1:
        await message.answer("Количество должно быть от 1.")
        return
    await state.update_data(acc_count=cnt)
    await state.set_state(GenericCalc.accident_repeat)
    await message.answer(
        "Это второй/последующий договор у этого страхователя? (AB3.1)",
        reply_markup=_yes_no_keyboard("acc:repeat"),
    )


@router.callback_query(GenericCalc.accident_repeat, F.data.startswith("acc:repeat:"))
async def accident_repeat_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    mark = callback.data.split(":")[-1]
    if mark == "yes":
        b = True
    elif mark == "no":
        b = False
    else:
        await callback.answer("Ответь: да или нет", show_alert=True)
        return
    await state.update_data(acc_repeat=b)
    await state.set_state(GenericCalc.accident_epolicy)
    await callback.message.answer(
        "Договор оформляется в электронном виде (AB3.2)?",
        reply_markup=_yes_no_keyboard("acc:epolicy"),
    )
    await callback.answer()


@router.message(GenericCalc.accident_repeat)
async def accident_repeat(message: Message, state: FSMContext) -> None:
    b = _yes_no(message.text or "")
    if b is None:
        await message.answer("Ответь: да или нет.")
        return
    await state.update_data(acc_repeat=b)
    await state.set_state(GenericCalc.accident_epolicy)
    await message.answer(
        "Договор оформляется в электронном виде (AB3.2)?",
        reply_markup=_yes_no_keyboard("acc:epolicy"),
    )


@router.callback_query(GenericCalc.accident_epolicy, F.data.startswith("acc:epolicy:"))
async def accident_epolicy_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    mark = callback.data.split(":")[-1]
    if mark == "yes":
        b = True
    elif mark == "no":
        b = False
    else:
        await callback.answer("Ответь: да или нет", show_alert=True)
        return
    await state.update_data(acc_epolicy=b)
    data = await state.get_data()

    inp = AccidentTravelInput(
        full_name=str(data.get("full_name", "")),
        contact=str(data.get("contact", "")),
        days=int(data["acc_days"]),
        age=int(data["acc_age"]),
        variant_a=bool(data["acc_variant_a"]),
        variant_b=bool(data["acc_variant_b"]),
        sum_a=int(data["acc_sum_a"]) if data.get("acc_sum_a") is not None else None,
        sum_b=int(data["acc_sum_b"]) if data.get("acc_sum_b") is not None else None,
        territory_option=int(data["acc_ab5"]),
        sport_training=bool(data["acc_sport"]),
        insured_count=int(data["acc_count"]),
        repeat_contract=bool(data["acc_repeat"]),
        e_policy=b,
    )
    quote = calculate_accident_travel(inp)

    payload = {
        "full_name": inp.full_name,
        "contact": inp.contact,
        "days": inp.days,
        "age": inp.age,
        "variant_a": inp.variant_a,
        "variant_b": inp.variant_b,
        "sum_a": inp.sum_a,
        "sum_b": inp.sum_b,
        "ab5_option": inp.territory_option,
        "sport_training": inp.sport_training,
        "insured_count": inp.insured_count,
        "repeat_contract": inp.repeat_contract,
        "e_policy": inp.e_policy,
    }
    saved = await create_generic_quote(
        callback.from_user.id,
        quote_type=QuoteType.accident,
        input_payload=payload,
        premium_byn=quote.premium,
        currency=quote.currency,
    )

    lines = [
        "✈️ Страховка за границу (расчёт по таблицам Правил №18)",
        f"👤 Имя: {inp.full_name}",
        f"📞 Контакт: {inp.contact}",
        f"🗓 Дней: {inp.days}",
        f"🎂 Возраст: {inp.age}",
        f"🧭 AB5: вариант {inp.territory_option}",
        f"💰 Итог: {quote.premium:.2f} {quote.currency}",
        "",
        "ℹ️ Расшифровка:",
    ]
    for name, val in quote.breakdown:
        if "Базовый тариф" in name:
            lines.append(f"- {name}: {val:.2f}")
        else:
            lines.append(f"- {name}: x{val:.3f}")
    lines.append("")
    lines.append("Примечание: AB4-коэффициенты (спец. акции/партнерские условия) в чат-версии не применяются.")
    lines.append(f"Расчёт сохранён (№{saved.id}). Можешь оформить заявку.")

    await state.clear()
    await callback.message.answer("\n".join(lines), reply_markup=apply_quote_keyboard(saved.id))
    await callback.message.answer("Главное меню", reply_markup=client_menu())
    await callback.answer()


@router.message(GenericCalc.accident_epolicy)
async def accident_epolicy(message: Message, state: FSMContext) -> None:
    b = _yes_no(message.text or "")
    if b is None:
        await message.answer("Ответь: да или нет.")
        return
    await state.update_data(acc_epolicy=b)
    data = await state.get_data()

    inp = AccidentTravelInput(
        full_name=str(data.get("full_name", "")),
        contact=str(data.get("contact", "")),
        days=int(data["acc_days"]),
        age=int(data["acc_age"]),
        variant_a=bool(data["acc_variant_a"]),
        variant_b=bool(data["acc_variant_b"]),
        sum_a=int(data["acc_sum_a"]) if data.get("acc_sum_a") is not None else None,
        sum_b=int(data["acc_sum_b"]) if data.get("acc_sum_b") is not None else None,
        territory_option=int(data["acc_ab5"]),
        sport_training=bool(data["acc_sport"]),
        insured_count=int(data["acc_count"]),
        repeat_contract=bool(data["acc_repeat"]),
        e_policy=b,
    )
    quote = calculate_accident_travel(inp)

    payload = {
        "full_name": inp.full_name,
        "contact": inp.contact,
        "days": inp.days,
        "age": inp.age,
        "variant_a": inp.variant_a,
        "variant_b": inp.variant_b,
        "sum_a": inp.sum_a,
        "sum_b": inp.sum_b,
        "ab5_option": inp.territory_option,
        "sport_training": inp.sport_training,
        "insured_count": inp.insured_count,
        "repeat_contract": inp.repeat_contract,
        "e_policy": inp.e_policy,
    }
    saved = await create_generic_quote(
        message.from_user.id,
        quote_type=QuoteType.accident,
        input_payload=payload,
        premium_byn=quote.premium,
        currency=quote.currency,
    )

    lines = [
        "✈️ Страховка за границу (расчёт по таблицам Правил №18)",
        f"👤 Имя: {inp.full_name}",
        f"📞 Контакт: {inp.contact}",
        f"🗓 Дней: {inp.days}",
        f"🎂 Возраст: {inp.age}",
        f"🧭 AB5: вариант {inp.territory_option}",
        f"💰 Итог: {quote.premium:.2f} {quote.currency}",
        "",
        "ℹ️ Расшифровка:",
    ]
    for name, val in quote.breakdown:
        if "Базовый тариф" in name:
            lines.append(f"- {name}: {val:.2f}")
        else:
            lines.append(f"- {name}: x{val:.3f}")
    lines.append("")
    lines.append("Примечание: AB4-коэффициенты (спец. акции/партнерские условия) в чат-версии не применяются.")
    lines.append(f"Расчёт сохранён (№{saved.id}). Можешь оформить заявку.")

    await state.clear()
    await message.answer("\n".join(lines), reply_markup=apply_quote_keyboard(saved.id))
    await message.answer("Главное меню", reply_markup=client_menu())


@router.message(GenericCalc.subject)
async def step_subject(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("Опиши объект чуть подробнее.")
        return
    await state.update_data(subject=text)
    await state.set_state(GenericCalc.value)
    await message.answer("Стоимость (BYN), число. Например: 50000")


@router.message(GenericCalc.value)
async def step_value(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        await message.answer("Стоимость должна быть числом, например: 50000")
        return
    if value <= 0:
        await message.answer("Стоимость должна быть больше 0.")
        return
    await state.update_data(value=value)
    await state.set_state(GenericCalc.comment)
    await message.answer("Комментарий (можно “нет”).")


@router.message(GenericCalc.comment)
async def step_comment(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text.lower() == "нет":
        text = ""
    await state.update_data(comment=text)

    data = await state.get_data()
    kind = str(data.get("kind", "other"))
    other_kind = data.get("other_kind")

    inp = GenericInput(
        full_name=str(data.get("full_name", "")),
        contact=str(data.get("contact", "")),
        subject=str(data.get("subject", "")),
        insured_value=float(data.get("value", 0)),
        comment=str(data.get("comment") or "") or None,
        extra_type=str(other_kind) if other_kind else None,
    )
    quote = calculate_generic(kind, inp)

    payload = {
        "full_name": inp.full_name,
        "contact": inp.contact,
        "subject": inp.subject,
        "insured_value": inp.insured_value,
        "comment": inp.comment,
        "extra_type": inp.extra_type,
    }
    qt = QuoteType.other
    if kind == "cargo":
        qt = QuoteType.cargo
    elif kind == "accident":
        qt = QuoteType.accident
    elif kind == "expeditor":
        qt = QuoteType.expeditor
    elif kind == "cmr":
        qt = QuoteType.cmr
    elif kind == "dms":
        qt = QuoteType.dms

    saved = await create_generic_quote(message.from_user.id, quote_type=qt, input_payload=payload, premium_byn=quote.premium, currency=quote.currency)

    lines = [
        f"{_kind_title(kind, other_kind)} (тестовый расчёт)",
        f"👤 Имя: {inp.full_name}",
        f"📞 Контакт: {inp.contact}",
        f"🏷 Что страхуем: {inp.subject}",
        f"💰 Стоимость: {inp.insured_value:.2f} BYN",
        f"💰 Итог: {quote.premium:.2f} {quote.currency}",
        "",
        "ℹ️ Расшифровка коэффициентов:",
    ]
    for name, coef in quote.breakdown:
        if name.startswith("Базовая ставка"):
            lines.append(f"- {name}: {coef*100:.2f}%")
        else:
            lines.append(f"- {name}: x{coef:.2f}")
    lines.append("")
    lines.append(f"Расчёт сохранён (№{saved.id}). Можешь оформить заявку.")

    await state.clear()
    await message.answer("\n".join(lines), reply_markup=apply_quote_keyboard(saved.id))
    await message.answer("Главное меню", reply_markup=client_menu())

