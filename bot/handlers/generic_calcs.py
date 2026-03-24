from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from bot.db.models import QuoteType, UserRole
from bot.db.repo import create_generic_quote, get_or_create_user
from bot.keyboards import apply_quote_keyboard, client_menu
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
        await callback.message.answer("✍️ Укажите вид страхования (текстом). Можно “отмена”.", reply_markup=ReplyKeyboardRemove())
        return
    await state.set_state(GenericCalc.full_name)
    await callback.message.answer(
        f"{_kind_title(kind)} — расчёт.\nКак вас зовут? Можно “отмена”.",
        reply_markup=ReplyKeyboardRemove(),
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
        await message.answer("Сколько дней поездка? (1-366)")
        return
    if str(data.get("kind")) == "expeditor":
        await state.set_state(GenericCalc.expeditor_plan)
        await message.answer(
            "Выберите пакет страхования ответственности экспедитора:\n"
            "1) Базовый — лимит на 1 случай 50 000, агрегатный лимит 250 000, франшиза 500\n"
            "2) Стандарт — лимит на 1 случай 100 000, агрегатный лимит 500 000, франшиза 1 000\n"
            "3) Премиум — лимит на 1 случай 250 000, агрегатный лимит 750 000, франшиза 2 000\n"
            "4) Максимальный — лимит на 1 случай 500 000, агрегатный лимит 750 000, франшиза 1 500\n"
            "Напишите номер 1-4 или название пакета."
        )
        return
    await state.set_state(GenericCalc.subject)
    await message.answer("Что вы хотите застраховать? (текстом)")


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
    await message.answer("Меню.", reply_markup=client_menu())


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
    if days < 1 or days > 366:
        await message.answer("Срок поездки должен быть от 1 до 366 дней.")
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
        "или B (5 000, 10 000 или 20 000)."
    )


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
        await message.answer("Страховая сумма по A: 30000 / 50000 / 70000 / 100000")
    else:
        await state.set_state(GenericCalc.accident_sum_b)
        await message.answer("Страховая сумма по B: 5000 / 10000 / 20000")


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
    data = await state.get_data()
    if bool(data.get("acc_variant_b")):
        await state.set_state(GenericCalc.accident_sum_b)
        await message.answer("Страховая сумма по B: 5000 / 10000 / 20000")
        return
    await state.set_state(GenericCalc.accident_territory)
    await message.answer(
        "Выбери вариант территории AB5 (1-5):\n"
        "1 — любые страны мира, кроме Индии, Индонезии, Таиланда, Израиля, США, Канады\n"
        "2 — все страны мира, кроме Индии, Индонезии, Таиланда, Израиля, США, Канады\n"
        "3 — Индия, Индонезия, Таиланд\n"
        "4 — Израиль, США, Канада\n"
        "5 — все страны мира (по всему миру)"
    )


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
        "5 — все страны мира (по всему миру)"
    )


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
    await message.answer("Есть спорт/тренировки (A2)? да/нет")


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
    await message.answer("Это второй/последующий договор у этого страхователя? (AB3.1) да/нет")


@router.message(GenericCalc.accident_repeat)
async def accident_repeat(message: Message, state: FSMContext) -> None:
    b = _yes_no(message.text or "")
    if b is None:
        await message.answer("Ответь: да или нет.")
        return
    await state.update_data(acc_repeat=b)
    await state.set_state(GenericCalc.accident_epolicy)
    await message.answer("Договор оформляется в электронном виде (AB3.2)? да/нет")


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
    await message.answer("Меню.", reply_markup=client_menu())


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
    await message.answer("Меню.", reply_markup=client_menu())

