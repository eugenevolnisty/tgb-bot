from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from bot.db.models import QuoteType, UserRole
from bot.db.repo import create_generic_quote, get_or_create_user
from bot.keyboards import apply_quote_keyboard, client_menu
from bot.services.generic_calc import GenericInput, calculate_generic

router = Router()


class GenericCalc(StatesGroup):
    other_kind = State()
    full_name = State()
    contact = State()
    subject = State()
    value = State()
    comment = State()


async def _ensure_client(message: Message) -> bool:
    user = await get_or_create_user(message.from_user.id)
    return user.role == UserRole.client


async def _ensure_client_tg(tg_id: int) -> bool:
    user = await get_or_create_user(tg_id)
    return user.role == UserRole.client


def _kind_title(kind: str, other_kind: str | None = None) -> str:
    m = {
        "cargo": "📦 Грузы",
        "accident": "🩹 Несчастные случаи",
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
    await state.set_state(GenericCalc.subject)
    await message.answer("Что вы хотите застраховать? (текстом)")


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

