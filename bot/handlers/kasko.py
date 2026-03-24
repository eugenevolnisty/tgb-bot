from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from bot.db.models import UserRole
from bot.db.repo import create_application_from_quote, create_kasko_quote, get_or_create_user
from bot.keyboards import Btn, apply_quote_keyboard, client_menu, insurance_type_keyboard
from bot.services.kasko import KaskoInput, calculate_kasko

router = Router()


class KaskoCalc(StatesGroup):
    full_name = State()
    contact = State()
    brand_model = State()
    year = State()
    car_value = State()
    abroad = State()
    drivers_count = State()
    youngest_driver_age = State()


async def _ensure_client(message: Message) -> bool:
    user = await get_or_create_user(message.from_user.id)
    return user.role == UserRole.client


async def _ensure_client_tg(tg_id: int) -> bool:
    user = await get_or_create_user(tg_id)
    return user.role == UserRole.client


def _normalize_bool_ru(text: str) -> bool | None:
    t = text.strip().lower()
    if t in {"да", "yes", "y", "true", "1"}:
        return True
    if t in {"нет", "no", "n", "false", "0"}:
        return False
    return None


@router.message(F.text == Btn.CALC_PRICE)
async def start_calc_menu(message: Message, state: FSMContext) -> None:
    if not await _ensure_client(message):
        return
    await state.clear()
    await message.answer(
        "🧮 Выберите вид страхования для расчёта:",
        reply_markup=insurance_type_keyboard(prefix="calc"),
    )


@router.callback_query(F.data.startswith("calc:type:"))
async def pick_calc_type(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not (await _ensure_client_tg(callback.from_user.id)):
        await callback.answer("Недоступно", show_alert=True)
        return

    t = callback.data.split(":", 2)[2]
    if t == "property":
        await state.clear()
        # Делегируем в FSM имущества: выставляем состояние и просим имя.
        from bot.handlers.property_calc import PropertyCalc

        await state.set_state(PropertyCalc.full_name)
        await callback.message.answer(
            "🏠 Расчёт страхования имущества.\nКак вас зовут? Можно написать “отмена”.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await callback.answer()
        return

    if t in {"cargo", "accident", "expeditor", "cmr", "dms", "other"}:
        await state.clear()
        from bot.handlers.generic_calcs import start_generic_from_callback

        await start_generic_from_callback(t, callback, state)
        await callback.answer()
        return

    if t != "kasko":
        await state.clear()
        await callback.message.answer("Неизвестный вид страхования.", reply_markup=client_menu())
        await callback.answer()
        return

    await state.clear()
    await state.set_state(KaskoCalc.full_name)
    await callback.message.answer(
        "🧮 Расчёт КАСКО.\nКак вас зовут? Можно написать “отмена”.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await callback.answer()
@router.message(KaskoCalc.full_name)
async def step_full_name(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 2:
        await message.answer("Напиши имя (например: Евгений).")
        return
    await state.update_data(full_name=text)
    await state.set_state(KaskoCalc.contact)
    await message.answer("Как с вами связаться? (телефон / Telegram / e-mail)")


@router.message(KaskoCalc.contact)
async def step_contact(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("Укажи контакт (например: +375... или @username).")
        return
    await state.update_data(contact=text)
    await state.set_state(KaskoCalc.brand_model)
    await message.answer("Марка и модель авто (например: Toyota Camry).")



@router.message(F.text.casefold() == "отмена")
async def cancel_any(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        return
    await state.clear()
    await message.answer("Ок, отменил расчёт.", reply_markup=client_menu())


@router.message(KaskoCalc.brand_model)
async def step_brand_model(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 2:
        await message.answer("Напиши марку и модель текстом, например: Mazda 6.")
        return
    await state.update_data(brand_model=text)
    await state.set_state(KaskoCalc.year)
    await message.answer(f"Год выпуска? (например: {date.today().year - 3})")


@router.message(KaskoCalc.year)
async def step_year(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    try:
        year = int(text)
    except ValueError:
        await message.answer("Год должен быть числом, например: 2018.")
        return

    current_year = date.today().year
    if year < 1980 or year > current_year:
        await message.answer(f"Введи год в диапазоне 1980–{current_year}.")
        return

    await state.update_data(year=year)
    await state.set_state(KaskoCalc.car_value)
    await message.answer("Стоимость авто (BYN), число. Например: 35000")


@router.message(KaskoCalc.car_value)
async def step_car_value(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        await message.answer("Стоимость должна быть числом, например: 35000")
        return
    if value <= 0:
        await message.answer("Стоимость должна быть больше 0.")
        return

    await state.update_data(car_value=value)
    await state.set_state(KaskoCalc.abroad)
    await message.answer("Нужен выезд за границу? (да/нет)")


@router.message(KaskoCalc.abroad)
async def step_abroad(message: Message, state: FSMContext) -> None:
    b = _normalize_bool_ru(message.text or "")
    if b is None:
        await message.answer("Ответь “да” или “нет”.")
        return

    await state.update_data(abroad=b)
    await state.set_state(KaskoCalc.drivers_count)
    await message.answer("Сколько водителей будет в полисе? (число, например: 1 или 2)")


@router.message(KaskoCalc.drivers_count)
async def step_drivers_count(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    try:
        n = int(text)
    except ValueError:
        await message.answer("Введи целое число, например: 1 или 2.")
        return
    if n <= 0 or n > 10:
        await message.answer("Введи количество водителей от 1 до 10.")
        return

    await state.update_data(drivers_count=n)
    await state.set_state(KaskoCalc.youngest_driver_age)
    await message.answer("Минимальный возраст водителя (полных лет), например: 24")


@router.message(KaskoCalc.youngest_driver_age)
async def step_youngest_age(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    try:
        age = int(text)
    except ValueError:
        await message.answer("Возраст должен быть числом, например: 24.")
        return
    if age < 16 or age > 90:
        await message.answer("Введи возраст в диапазоне 16–90.")
        return

    data = await state.get_data()
    full_name = str(data.get("full_name", "")).strip() or None
    contact = str(data.get("contact", "")).strip() or None
    inp = KaskoInput(
        brand_model=str(data["brand_model"]),
        year=int(data["year"]),
        car_value=float(data["car_value"]),
        abroad=bool(data["abroad"]),
        drivers_count=int(data["drivers_count"]),
        youngest_driver_age=age,
    )
    quote = calculate_kasko(inp)

    lines = [
        "🧮 КАСКО (тестовый расчёт)",
        f"👤 Имя: {full_name or '—'}",
        f"📞 Контакт: {contact or '—'}",
        f"Авто: {inp.brand_model}, {inp.year}",
        f"Стоимость: {inp.car_value:,.2f} BYN".replace(",", " "),
        f"💰 Итог: {quote.premium:,.2f} {quote.currency}".replace(",", " "),
        "",
        "ℹ️ Расшифровка коэффициентов:",
    ]
    for name, coef in quote.breakdown:
        if name.startswith("Базовая ставка"):
            lines.append(f"- {name}: {coef*100:.2f}%")
        else:
            lines.append(f"- {name}: x{coef:.2f}")

    input_payload = {
        "full_name": full_name,
        "contact": contact,
        "brand_model": inp.brand_model,
        "year": inp.year,
        "car_value": inp.car_value,
        "abroad": inp.abroad,
        "drivers_count": inp.drivers_count,
        "youngest_driver_age": inp.youngest_driver_age,
    }
    saved = await create_kasko_quote(message.from_user.id, input_payload=input_payload, premium_byn=quote.premium, currency=quote.currency)
    lines.append("")
    lines.append(f"Расчёт сохранён (№{saved.id}). Можешь оформить заявку.")

    await state.clear()
    await message.answer("\n".join(lines), reply_markup=apply_quote_keyboard(saved.id))
    await message.answer("Меню.", reply_markup=client_menu())


@router.callback_query(F.data.startswith("quote_apply:"))
async def apply_quote(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not (await _ensure_client_tg(callback.from_user.id)):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        quote_id = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    app = await create_application_from_quote(callback.from_user.id, quote_id=quote_id)
    await callback.message.answer(f"Готово! Заявка №{app.id} создана и отправлена агенту.", reply_markup=client_menu())
    await callback.answer("Заявка создана")

