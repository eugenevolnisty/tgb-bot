from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.db.models import UserRole
from bot.db.repo import create_application_for_client, get_or_create_user
from bot.keyboards import Btn, client_menu, insurance_type_keyboard, to_main_menu_keyboard

router = Router()


class AppForm(StatesGroup):
    entity_type = State()  # физ/юр
    insurance_type = State()
    insurance_type_other = State()
    subject = State()
    cost = State()
    comment = State()
    full_name = State()
    contact = State()


async def _ensure_client(message: Message) -> bool:
    user = await get_or_create_user(message.from_user.id)
    return user.role == UserRole.client


def entity_keyboard() -> "aiogram.types.InlineKeyboardMarkup":
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👤 Физ. лицо", callback_data="app:entity:person"),
                InlineKeyboardButton(text="🏢 Юр. лицо", callback_data="app:entity:company"),
            ]
        ]
    )


@router.message(F.text == Btn.LEAVE_APP)
async def start_application(message: Message, state: FSMContext) -> None:
    if not await _ensure_client(message):
        return
    await state.clear()
    await state.set_state(AppForm.entity_type)
    await message.answer(
        "📝 Заявка.\nВыберите тип клиента:",
        reply_markup=entity_keyboard(),
    )


@router.message(F.text.casefold() == "отмена")
async def cancel_any(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        return
    await state.clear()
    await message.answer("Ок, отменил.", reply_markup=client_menu())


@router.callback_query(F.data.in_({"app:entity:person", "app:entity:company"}))
async def pick_entity(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    entity = "Физ. лицо" if callback.data.endswith("person") else "Юр. лицо"
    await state.update_data(entity_type=entity)
    await state.set_state(AppForm.insurance_type)
    await callback.message.answer("Выберите вид страхования:", reply_markup=insurance_type_keyboard(prefix="app"))
    await callback.answer()


@router.callback_query(F.data.startswith("app:type:"))
async def pick_insurance_type(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    t = callback.data.split(":", 2)[2]
    mapping = {
        "kasko": "КАСКО",
        "property": "Имущество",
        "cargo": "Грузы",
        "accident": "Страховка за границу",
        "expeditor": "Ответственность экспедитора",
        "cmr": "CMR",
        "dms": "ДМС",
        "other": "Другой вид",
    }
    title = mapping.get(t, "Другой вид")
    await state.update_data(insurance_type=title)
    if t == "other":
        await state.set_state(AppForm.insurance_type_other)
        await callback.message.answer("✍️ Укажите, какой именно вид страхования (текстом). Можно “отмена”.")
    else:
        await state.set_state(AppForm.subject)
        await callback.message.answer(
            "3) Введите, что вы хотите застраховать (текстом). Можно “отмена”.",
            reply_markup=to_main_menu_keyboard(),
        )
    await callback.answer()


@router.message(AppForm.insurance_type_other)
async def step_type_other(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 2:
        await message.answer("Укажи вид страхования чуть подробнее.")
        return
    await state.update_data(insurance_type_other=text)
    await state.set_state(AppForm.subject)
    await message.answer("3) Введите, что вы хотите застраховать (текстом).")


@router.message(AppForm.subject)
async def step_subject(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("Опиши объект страхования чуть подробнее.")
        return
    await state.update_data(subject=text)
    await state.set_state(AppForm.cost)
    await message.answer("4) Введите стоимость (число, BYN). Например: 35000")


@router.message(AppForm.cost)
async def step_cost(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        await message.answer("Стоимость должна быть числом, например: 35000")
        return
    if value <= 0:
        await message.answer("Стоимость должна быть больше 0.")
        return
    await state.update_data(cost=value)
    await state.set_state(AppForm.comment)
    await message.answer("5) Ваш комментарий (можно коротко). Если нет — напишите “нет”.")


@router.message(AppForm.comment)
async def step_comment(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text.lower() == "нет":
        text = ""
    await state.update_data(comment=text)
    await state.set_state(AppForm.full_name)
    await message.answer("6) Как вас зовут?")


@router.message(AppForm.full_name)
async def step_full_name(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 2:
        await message.answer("Напиши имя, например: Евгений.")
        return
    await state.update_data(full_name=text)
    await state.set_state(AppForm.contact)
    await message.answer("7) Как с вами связаться? (телефон / Telegram / e-mail)")


@router.message(AppForm.contact)
async def step_contact(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("Укажи контакт (например: +375... или @username).")
        return
    await state.update_data(contact=text)

    data = await state.get_data()
    insurance_type = str(data.get("insurance_type", "—"))
    if insurance_type == "Другой вид":
        insurance_type = f"Другой: {data.get('insurance_type_other', '—')}"

    description_lines = [
        "📝 Новая заявка",
        f"Тип клиента: {data.get('entity_type', '—')}",
        f"Вид: {insurance_type}",
        "",
        f"Что страхуем: {data.get('subject', '—')}",
        f"Стоимость: {data.get('cost', '—')} BYN",
    ]
    comment = str(data.get("comment", "")).strip()
    if comment:
        description_lines.append(f"Комментарий: {comment}")
    description_lines += [
        "",
        f"Имя: {data.get('full_name', '—')}",
        f"Контакт: {data.get('contact', '—')}",
    ]

    title = f"Заявка — {insurance_type}"
    app = await create_application_for_client(message.from_user.id, description="\n".join(description_lines))

    await state.clear()
    await message.answer(f"✅ Заявка №{app.id} создана и отправлена агенту.", reply_markup=client_menu())

