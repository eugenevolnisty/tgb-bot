from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot.db.models import UserRole
from bot.db.repo import create_property_quote, get_or_create_user
from bot.keyboards import apply_quote_keyboard, client_menu, to_main_menu_keyboard
from bot.services.property import PropertyInput, calculate_property

router = Router()


class PropertyCalc(StatesGroup):
    full_name = State()
    contact = State()
    subject = State()
    address = State()
    value = State()
    comment = State()


async def _ensure_client(message: Message) -> bool:
    user = await get_or_create_user(message.from_user.id)
    return user.role in {UserRole.client, UserRole.superadmin}


@router.message(PropertyCalc.full_name)
async def step_full_name(message: Message, state: FSMContext) -> None:
    if not await _ensure_client(message):
        return
    text = (message.text or "").strip()
    if len(text) < 2:
        await message.answer("Напиши имя, например: Евгений.")
        return
    await state.update_data(full_name=text)
    await state.set_state(PropertyCalc.contact)
    await message.answer("📞 Как с вами связаться? (телефон / Telegram / e-mail)")


@router.message(PropertyCalc.contact)
async def step_contact(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("Укажи контакт (например: +375... или @username).")
        return
    await state.update_data(contact=text)
    await state.set_state(PropertyCalc.subject)
    await message.answer("Что вы хотите застраховать? (например: квартира, дом, дача)")


@router.message(PropertyCalc.subject)
async def step_subject(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("Опиши объект чуть подробнее.")
        return
    await state.update_data(subject=text)
    await state.set_state(PropertyCalc.address)
    await message.answer("Город/адрес объекта (можно просто город).")


@router.message(PropertyCalc.address)
async def step_address(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 2:
        await message.answer("Укажи город или адрес.")
        return
    await state.update_data(address=text)
    await state.set_state(PropertyCalc.value)
    await message.answer("Стоимость имущества (BYN), число. Например: 120000")


@router.message(PropertyCalc.value)
async def step_value(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        await message.answer("Стоимость должна быть числом, например: 120000")
        return
    if value <= 0:
        await message.answer("Стоимость должна быть больше 0.")
        return
    await state.update_data(value=value)
    await state.set_state(PropertyCalc.comment)
    await message.answer("Комментарий (можно “нет”).", reply_markup=to_main_menu_keyboard())


@router.message(PropertyCalc.comment)
async def step_comment(message: Message, state: FSMContext, is_superadmin: bool = False) -> None:
    text = (message.text or "").strip()
    if text.lower() == "нет":
        text = ""
    await state.update_data(comment=text)

    data = await state.get_data()
    inp = PropertyInput(
        full_name=str(data["full_name"]),
        contact=str(data["contact"]),
        subject=str(data["subject"]),
        address_or_city=str(data["address"]),
        property_value=float(data["value"]),
        comment=str(data.get("comment") or "") or None,
    )
    quote = calculate_property(inp)

    input_payload = {
        "full_name": inp.full_name,
        "contact": inp.contact,
        "subject": inp.subject,
        "address_or_city": inp.address_or_city,
        "property_value": inp.property_value,
        "comment": inp.comment,
    }
    saved = await create_property_quote(message.from_user.id, input_payload=input_payload, premium_byn=quote.premium, currency=quote.currency)

    lines = [
        "🏠 Имущество (тестовый расчёт)",
        f"👤 Имя: {inp.full_name}",
        f"📞 Контакт: {inp.contact}",
        f"📍 Локация: {inp.address_or_city}",
        f"🏷 Что страхуем: {inp.subject}",
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
    await message.answer("Главное меню", reply_markup=client_menu(show_back_to_admin=is_superadmin))

