from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db.models import UserRole
from bot.db.repo import get_or_create_user, set_user_role
from bot.keyboards import Btn, agent_menu, client_menu, role_keyboard

router = Router()


async def _delete_user_button_message(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


async def _set_reply_keyboard_silent(message: Message, *, text: str, reply_markup) -> None:
    await message.answer(text, reply_markup=reply_markup)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = await get_or_create_user(message.from_user.id)
    if user.role == UserRole.agent:
        await message.answer("Вы в роли агента.", reply_markup=agent_menu())
        return
    if user.role == UserRole.client:
        await message.answer("Вы в роли клиента.", reply_markup=client_menu())
        return

    await message.answer(
        "Здравствуйте! Кто вы?",
        reply_markup=role_keyboard(),
    )


@router.callback_query(F.data.in_({"role:agent", "role:client"}))
async def choose_role(callback: CallbackQuery) -> None:
    role = UserRole.agent if callback.data == "role:agent" else UserRole.client
    await set_user_role(callback.from_user.id, role)

    if role == UserRole.agent:
        await callback.message.answer("Роль сохранена: агент.", reply_markup=agent_menu())
    else:
        await callback.message.answer("Роль сохранена: клиент.", reply_markup=client_menu())

    await callback.answer()


@router.message(F.text.in_({Btn.SWITCH_TO_AGENT, Btn.SWITCH_TO_CLIENT}))
async def switch_role(message: Message) -> None:
    user = await get_or_create_user(message.from_user.id)
    if user.role == UserRole.agent:
        await set_user_role(message.from_user.id, UserRole.client)
        await message.answer("Переключил роль: клиент.", reply_markup=client_menu())
        return
    await set_user_role(message.from_user.id, UserRole.agent)
    await message.answer("Переключил роль: агент.", reply_markup=agent_menu())


@router.message(F.text == Btn.MAIN_MENU)
async def to_main_menu(message: Message, state: FSMContext) -> None:
    await _delete_user_button_message(message)
    if await state.get_state() is not None:
        await state.clear()
    user = await get_or_create_user(message.from_user.id)
    if user.role == UserRole.agent:
        await _set_reply_keyboard_silent(message, text="Выберите действие.", reply_markup=agent_menu())
        return
    if user.role == UserRole.client:
        await _set_reply_keyboard_silent(message, text="Выберите действие.", reply_markup=client_menu())
        return
    await message.answer("Здравствуйте! Кто вы?", reply_markup=role_keyboard())
