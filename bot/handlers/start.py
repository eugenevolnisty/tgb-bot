from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import get_settings
from bot.db.models import UserRole
from bot.db.repo import (
    consume_agent_invite,
    consume_public_agent_link,
    get_bound_agent_and_client_for_user,
    get_or_create_user,
    get_user_display_name,
    has_agent_footprint,
    has_agent_password,
    set_user_role,
    set_agent_display_name,
    verify_agent_password,
)
from bot.keyboards import Btn, agent_menu, client_menu, role_keyboard
from bot.services.agent_auth import authorize_agent_session, is_agent_session_active, revoke_agent_session

router = Router()


class AgentLogin(StatesGroup):
    password = State()


class AgentProfileSetup(StatesGroup):
    name = State()


async def _delete_user_button_message(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


async def _set_reply_keyboard_silent(message: Message, *, text: str, reply_markup) -> None:
    await message.answer(text, reply_markup=reply_markup)


async def _can_switch_to_agent(tg_id: int) -> bool:
    # Temporary policy: allow only primary tester account(s) that already have agent setup/footprint.
    return get_settings().dev_role_switch_enabled and (await has_agent_password(tg_id) or await has_agent_footprint(tg_id))


async def _client_menu_for_user(tg_id: int):
    return client_menu(allow_switch_to_agent=await _can_switch_to_agent(tg_id))


async def _agent_display_name_or_default(agent_tg_id: int) -> str:
    n = await get_user_display_name(agent_tg_id)
    return n or "Ваш агент"


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = await get_or_create_user(message.from_user.id)
    start_parts = (message.text or "").strip().split(maxsplit=1)
    start_arg = start_parts[1].strip() if len(start_parts) > 1 else ""
    if start_arg.startswith("public_"):
        if user.role == UserRole.agent:
            await message.answer("Публичная ссылка предназначена для клиентов.")
            return
        ok, reason = await consume_public_agent_link(
            start_arg.removeprefix("public_"),
            message.from_user.id,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            username=message.from_user.username,
        )
        if ok:
            await set_user_role(message.from_user.id, UserRole.client)
            await state.clear()
            bound = await get_bound_agent_and_client_for_user(message.from_user.id)
            agent_name = "Ваш агент"
            if bound is not None:
                agent_name = await _agent_display_name_or_default(bound[0])
            await message.answer(
                f"✅ Вы подключены к агенту: {agent_name}. Открываю меню клиента.",
                reply_markup=await _client_menu_for_user(message.from_user.id),
            )
            if bound is not None:
                agent_tg_id, client_id = bound
                try:
                    fn = (message.from_user.first_name or "").strip()
                    ln = (message.from_user.last_name or "").strip()
                    un = (message.from_user.username or "").strip()
                    full = " ".join([x for x in [fn, ln] if x]).strip() or (f"@{un}" if un else "—")
                    username_line = f"@{un}" if un else "—"
                    info_text = (
                        "✅ Подключился новый клиент по вашей ссылке.\n"
                        f"Имя: {full}\n"
                        f"Username: {username_line}\n"
                        f"tg_id: {message.from_user.id}"
                    )
                    await message.bot.send_message(
                        agent_tg_id,
                        info_text,
                        reply_markup=InlineKeyboardMarkup(
                            inline_keyboard=[
                                [InlineKeyboardButton(text="Открыть карточку клиента", callback_data=f"client:open:{client_id}")]
                            ]
                        ),
                    )
                except Exception:
                    pass
            return
        reason_map = {
            "invite_not_found": "Ссылка не найдена.",
            "invite_not_active": "Ссылка не активна.",
            "user_in_other_tenant": "Ваш аккаунт уже привязан к другому агенту.",
            "user_already_bound": "Ваш аккаунт уже привязан к клиентской карточке.",
        }
        await message.answer(f"❌ {reason_map.get(reason, 'Не удалось применить ссылку.')}")
        return
    if start_arg.startswith("inv_"):
        if user.role == UserRole.agent:
            await message.answer("Инвайт-ссылка предназначена для клиента. Вы уже в роли агента.")
            return
        ok, reason = await consume_agent_invite(
            start_arg.removeprefix("inv_"),
            message.from_user.id,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            username=message.from_user.username,
        )
        if ok:
            await set_user_role(message.from_user.id, UserRole.client)
            await state.clear()
            bound = await get_bound_agent_and_client_for_user(message.from_user.id)
            agent_name = "Ваш агент"
            if bound is not None:
                agent_name = await _agent_display_name_or_default(bound[0])
            await message.answer(
                f"✅ Вы подключены к агенту: {agent_name}. Открываю меню клиента.",
                reply_markup=await _client_menu_for_user(message.from_user.id),
            )
            if bound is not None:
                agent_tg_id, client_id = bound
                try:
                    fn = (message.from_user.first_name or "").strip()
                    ln = (message.from_user.last_name or "").strip()
                    un = (message.from_user.username or "").strip()
                    full = " ".join([x for x in [fn, ln] if x]).strip() or (f"@{un}" if un else "—")
                    username_line = f"@{un}" if un else "—"
                    info_text = (
                        "✅ Клиент успешно привязался по персональной ссылке.\n"
                        f"Имя: {full}\n"
                        f"Username: {username_line}\n"
                        f"tg_id: {message.from_user.id}"
                    )
                    await message.bot.send_message(
                        agent_tg_id,
                        info_text,
                        reply_markup=InlineKeyboardMarkup(
                            inline_keyboard=[
                                [InlineKeyboardButton(text="Открыть карточку клиента", callback_data=f"client:open:{client_id}")]
                            ]
                        ),
                    )
                except Exception:
                    pass
            return
        reason_map = {
            "invite_not_found": "Инвайт не найден.",
            "invite_not_active": "Инвайт уже не активен.",
            "invite_expired": "Срок действия инвайта истек.",
            "invite_depleted": "Инвайт уже использован.",
            "user_in_other_tenant": "Ваш аккаунт уже привязан к другому агенту.",
            "user_already_bound": "Ваш аккаунт уже привязан к клиентской карточке.",
            "target_client_not_found": "Клиент для привязки не найден.",
            "target_client_already_bound": "Этот клиент уже привязан к другому Telegram-аккаунту.",
        }
        await message.answer(f"❌ {reason_map.get(reason, 'Не удалось применить инвайт.')}")
        return

    if user.role == UserRole.agent:
        if not (await get_user_display_name(message.from_user.id)):
            await state.set_state(AgentProfileSetup.name)
            await message.answer("Введите имя агента (как его увидят клиенты):")
            return
        if await has_agent_password(message.from_user.id) and not is_agent_session_active(message.from_user.id):
            await state.set_state(AgentLogin.password)
            await message.answer("🔐 Введите пароль агента:")
            return
        await state.clear()
        await message.answer("Вы в роли агента.", reply_markup=agent_menu())
        return
    if user.role == UserRole.client:
        await state.clear()
        await message.answer("Вы в роли клиента.", reply_markup=await _client_menu_for_user(message.from_user.id))
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
        if not (await get_user_display_name(callback.from_user.id)):
            await state.set_state(AgentProfileSetup.name)
            await callback.message.answer("Роль сохранена: агент.\nВведите имя агента (как его увидят клиенты):")
            await callback.answer()
            return
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
        await callback.message.answer(
            "Роль сохранена: клиент.",
            reply_markup=await _client_menu_for_user(callback.from_user.id),
        )

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
        await message.answer(
            "Переключил роль: клиент.",
            reply_markup=await _client_menu_for_user(message.from_user.id),
        )
        return
    if not await _can_switch_to_agent(message.from_user.id):
        await message.answer("Переключение на роль агента недоступно для этого аккаунта.")
        return
    await set_user_role(message.from_user.id, UserRole.agent)
    if not (await get_user_display_name(message.from_user.id)):
        await state.set_state(AgentProfileSetup.name)
        await message.answer("Введите имя агента (как его увидят клиенты):")
        return
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


@router.message(AgentProfileSetup.name)
async def agent_profile_name(message: Message, state: FSMContext) -> None:
    user = await get_or_create_user(message.from_user.id)
    if user.role != UserRole.agent:
        return
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Слишком коротко. Введите имя агента (минимум 2 символа).")
        return
    ok = await set_agent_display_name(message.from_user.id, name)
    if not ok:
        await message.answer("Не удалось сохранить имя агента.")
        return
    if await has_agent_password(message.from_user.id) and not is_agent_session_active(message.from_user.id):
        await state.set_state(AgentLogin.password)
        await message.answer("Имя сохранено. Теперь введите пароль агента:")
        return
    await state.clear()
    await message.answer(f"✅ Имя агента сохранено: {name}", reply_markup=agent_menu())


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
        await _set_reply_keyboard_silent(
            message,
            text="Выберите действие.",
            reply_markup=await _client_menu_for_user(message.from_user.id),
        )
        return
    await message.answer("Здравствуйте! Кто вы?", reply_markup=role_keyboard())
