from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings
from bot.db.models import UserRole
from bot.db.repo import consume_agent_invite, consume_public_agent_link, get_or_create_user, has_agent_password, set_user_role, verify_agent_password
from bot.keyboards import Btn, agent_menu, client_menu, role_keyboard
from bot.services.agent_auth import authorize_agent_session, is_agent_session_active, revoke_agent_session

router = Router()


class AgentLogin(StatesGroup):
    password = State()


async def _delete_user_button_message(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


async def _set_reply_keyboard_silent(message: Message, *, text: str, reply_markup) -> None:
    await message.answer(text, reply_markup=reply_markup)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = await get_or_create_user(message.from_user.id)
    start_parts = (message.text or "").strip().split(maxsplit=1)
    start_arg = start_parts[1].strip() if len(start_parts) > 1 else ""
    if start_arg.startswith("public_"):
        if user.role == UserRole.agent:
            await message.answer("Публичная ссылка предназначена для клиентов.")
            return
        ok, reason = await consume_public_agent_link(start_arg.removeprefix("public_"), message.from_user.id)
        if ok:
            await set_user_role(message.from_user.id, UserRole.client)
            await state.clear()
            await message.answer("✅ Вы подключены к агенту. Открываю меню клиента.", reply_markup=client_menu())
            return
        reason_map = {
            "invite_not_found": "Ссылка не найдена.",
            "invite_not_active": "Ссылка не активна.",
            "user_in_other_tenant": "Ваш аккаунт уже привязан к другому агенту.",
        }
        await message.answer(f"❌ {reason_map.get(reason, 'Не удалось применить ссылку.')}")
        return
    if start_arg.startswith("inv_"):
        if user.role == UserRole.agent:
            await message.answer("Инвайт-ссылка предназначена для клиента. Вы уже в роли агента.")
            return
        ok, reason = await consume_agent_invite(start_arg.removeprefix("inv_"), message.from_user.id)
        if ok:
            await set_user_role(message.from_user.id, UserRole.client)
            await state.clear()
            await message.answer("✅ Вы подключены к агенту. Открываю меню клиента.", reply_markup=client_menu())
            return
        reason_map = {
            "invite_not_found": "Инвайт не найден.",
            "invite_not_active": "Инвайт уже не активен.",
            "invite_expired": "Срок действия инвайта истек.",
            "invite_depleted": "Инвайт уже использован.",
            "user_in_other_tenant": "Ваш аккаунт уже привязан к другому агенту.",
            "target_client_not_found": "Клиент для привязки не найден.",
            "target_client_already_bound": "Этот клиент уже привязан к другому Telegram-аккаунту.",
        }
        await message.answer(f"❌ {reason_map.get(reason, 'Не удалось применить инвайт.')}")
        return

    if user.role == UserRole.agent:
        if await has_agent_password(message.from_user.id) and not is_agent_session_active(message.from_user.id):
            await state.set_state(AgentLogin.password)
            await message.answer("🔐 Введите пароль агента:")
            return
        await state.clear()
        await message.answer("Вы в роли агента.", reply_markup=agent_menu())
        return
    if user.role == UserRole.client:
        await state.clear()
        await message.answer("Вы в роли клиента.", reply_markup=client_menu())
        return

    await message.answer(
        "Здравствуйте! Кто вы?",
        reply_markup=role_keyboard(),
    )


@router.callback_query(F.data.in_({"role:agent", "role:client"}))
async def choose_role(callback: CallbackQuery, state: FSMContext) -> None:
    role = UserRole.agent if callback.data == "role:agent" else UserRole.client
    await set_user_role(callback.from_user.id, role)

    if role == UserRole.agent:
        if await has_agent_password(callback.from_user.id):
            await state.set_state(AgentLogin.password)
            await callback.message.answer("Роль сохранена: агент.\n🔐 Введите пароль агента:")
        else:
            authorize_agent_session(callback.from_user.id)
            await state.clear()
            await callback.message.answer("Роль сохранена: агент.", reply_markup=agent_menu())
    else:
        revoke_agent_session(callback.from_user.id)
        await state.clear()
        await callback.message.answer("Роль сохранена: клиент.", reply_markup=client_menu())

    await callback.answer()


@router.message(F.text.in_({Btn.SWITCH_TO_AGENT, Btn.SWITCH_TO_CLIENT}))
async def switch_role(message: Message, state: FSMContext) -> None:
    if not get_settings().dev_role_switch_enabled:
        await message.answer("Переключение ролей отключено в этом окружении.")
        return
    user = await get_or_create_user(message.from_user.id)
    if user.role == UserRole.agent:
        await set_user_role(message.from_user.id, UserRole.client)
        revoke_agent_session(message.from_user.id)
        await state.clear()
        await message.answer("Переключил роль: клиент.", reply_markup=client_menu())
        return
    await set_user_role(message.from_user.id, UserRole.agent)
    if await has_agent_password(message.from_user.id):
        await state.set_state(AgentLogin.password)
        await message.answer("🔐 Введите пароль агента:")
        return
    authorize_agent_session(message.from_user.id)
    await state.clear()
    await message.answer("Переключил роль: агент.", reply_markup=agent_menu())


@router.message(AgentLogin.password)
async def agent_login_password(message: Message, state: FSMContext) -> None:
    user = await get_or_create_user(message.from_user.id)
    if user.role != UserRole.agent:
        return
    if is_agent_session_active(message.from_user.id):
        await state.clear()
        return
    if not await has_agent_password(message.from_user.id):
        await state.clear()
        return
    ok = await verify_agent_password(message.from_user.id, (message.text or "").strip())
    if not ok:
        await message.answer("❌ Неверный пароль. Попробуйте снова.")
        return
    authorize_agent_session(message.from_user.id)
    if await state.get_state() is not None:
        await state.clear()
    await message.answer("✅ Вход выполнен.", reply_markup=agent_menu())


@router.message(Command("logout"))
async def cmd_logout(message: Message, state: FSMContext) -> None:
    user = await get_or_create_user(message.from_user.id)
    if user.role != UserRole.agent:
        await message.answer("Команда доступна только агенту.")
        return
    revoke_agent_session(message.from_user.id)
    await state.clear()
    await message.answer("🔒 Сессия агента завершена. Для входа используйте /login или /start.")


@router.message(Command("login"))
async def cmd_login(message: Message, state: FSMContext) -> None:
    user = await get_or_create_user(message.from_user.id)
    if user.role != UserRole.agent:
        await message.answer("Команда доступна только агенту.")
        return
    if not await has_agent_password(message.from_user.id):
        await message.answer("Пароль агента не задан. Установите его в Настройки → Доступ агента.")
        return
    if is_agent_session_active(message.from_user.id):
        await message.answer("Сессия уже активна.", reply_markup=agent_menu())
        return
    await state.set_state(AgentLogin.password)
    await message.answer("🔐 Введите пароль агента:")


@router.message(F.text == Btn.MAIN_MENU)
async def to_main_menu(message: Message, state: FSMContext) -> None:
    await _delete_user_button_message(message)
    if await state.get_state() is not None:
        await state.clear()
    user = await get_or_create_user(message.from_user.id)
    if user.role == UserRole.agent:
        if await has_agent_password(message.from_user.id) and not is_agent_session_active(message.from_user.id):
            await state.set_state(AgentLogin.password)
            await message.answer("🔐 Введите пароль агента:")
            return
        await state.clear()
        await _set_reply_keyboard_silent(message, text="Выберите действие.", reply_markup=agent_menu())
        return
    if user.role == UserRole.client:
        await state.clear()
        await _set_reply_keyboard_silent(message, text="Выберите действие.", reply_markup=client_menu())
        return
    await message.answer("Здравствуйте! Кто вы?", reply_markup=role_keyboard())
