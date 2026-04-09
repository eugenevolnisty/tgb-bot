from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import get_settings
from bot.db.models import AgentInvite, UserRole
from bot.db.repo import (
    consume_agent_invite,
    consume_agent_registration_invite,
    consume_public_agent_link,
    get_agent_invite_by_token,
    get_bound_agent_and_client_for_user,
    get_or_create_user,
    get_user_display_name,
    has_agent_password,
    set_user_role,
    set_agent_display_name,
    validate_private_invite_for_action,
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


async def _show_agent_menu(
    message_or_callback,
    *,
    is_superadmin: bool = False,
    text: str = "Выберите действие.",
) -> None:
    """Показывает агентское меню с правильными флагами."""
    target = (
        message_or_callback if isinstance(message_or_callback, Message) else message_or_callback.message
    )
    await target.answer(
        text,
        reply_markup=agent_menu(
            show_back_to_admin=is_superadmin,
            allow_switch_to_client=is_superadmin and get_settings().dev_role_switch_enabled,
        ),
    )


async def _show_client_menu(
    message_or_callback,
    tg_id: int,
    *,
    is_superadmin: bool = False,
    text: str = "Выберите действие.",
) -> None:
    """Показывает клиентское меню с правильными флагами."""
    target = (
        message_or_callback if isinstance(message_or_callback, Message) else message_or_callback.message
    )
    await target.answer(
        text,
        reply_markup=client_menu(
            allow_switch_to_agent=is_superadmin and get_settings().dev_role_switch_enabled,
            show_back_to_admin=is_superadmin,
        ),
    )


def _superadmin_main_kb(*, dev_switch_enabled: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="👑 Админ-панель", callback_data="sa:menu")]]
    if dev_switch_enabled:
        rows.append([InlineKeyboardButton(text="💼 Режим агента", callback_data="dev:role:agent")])
        rows.append([InlineKeyboardButton(text="👤 Режим клиента", callback_data="dev:role:client")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _is_superadmin_user(user_id: int) -> bool:
    settings = get_settings()
    return bool(settings.superadmin_tg_id) and user_id == settings.superadmin_tg_id


async def _show_superadmin_home(message: Message, *, text: str = "👑 Режим супер-админа.") -> None:
    settings = get_settings()
    await message.answer(
        text,
        reply_markup=_superadmin_main_kb(dev_switch_enabled=settings.dev_role_switch_enabled),
    )


async def _agent_display_name_or_default(agent_tg_id: int) -> str:
    n = await get_user_display_name(agent_tg_id)
    return n or "Ваш агент"


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, is_superadmin: bool = False) -> None:
    user = await get_or_create_user(message.from_user.id)
    start_parts = (message.text or "").strip().split(maxsplit=1)
    start_arg = start_parts[1].strip() if len(start_parts) > 1 else ""

    if user.role == UserRole.superadmin:
        await state.clear()
        await _show_superadmin_home(message)
        return

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
            await _show_client_menu(
                message,
                message.from_user.id,
                is_superadmin=is_superadmin,
                text=f"✅ Вы подключены к агенту: {agent_name}. Открываю меню клиента.",
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
        token = start_arg.removeprefix("inv_")
        invite_peek = await get_agent_invite_by_token(token)
        if invite_peek is None:
            await message.answer("❌ Инвайт не найден.")
            return
        inv_kind = getattr(invite_peek, "invite_type", None) or AgentInvite.INVITE_TYPE_CLIENT

        if inv_kind == AgentInvite.INVITE_TYPE_AGENT_REGISTRATION:
            invite, val_reason = await validate_private_invite_for_action(token)
            if invite is None:
                reason_map = {
                    "invite_not_found": "Инвайт не найден.",
                    "invite_not_active": "Инвайт уже не активен.",
                    "invite_expired": "Срок действия инвайта истек.",
                    "invite_depleted": "Инвайт уже использован.",
                }
                await message.answer(f"❌ {reason_map.get(val_reason, 'Не удалось применить инвайт.')}")
                return
            user = await get_or_create_user(message.from_user.id)
            if user.role == UserRole.agent:
                await state.clear()
                if await has_agent_password(message.from_user.id) and not is_agent_session_active(message.from_user.id):
                    await state.set_state(AgentLogin.password)
                    await message.answer("🔐 Введите пароль агента:")
                    return
                await _show_agent_menu(
                    message,
                    is_superadmin=is_superadmin,
                    text="Вы уже зарегистрированы как агент.",
                )
                return
            if user.role == UserRole.client:
                await message.answer(
                    "Этот инвайт для регистрации агента, а ваш аккаунт уже оформлен как клиент.",
                )
                return
            await set_user_role(message.from_user.id, UserRole.agent)
            await state.update_data(agent_registration_invite_token=token)
            await state.set_state(AgentProfileSetup.name)
            await message.answer(
                "Добро пожаловать! Вы получили приглашение как страховой агент.\n"
                "Введите ваше имя:",
            )
            return

        if user.role == UserRole.agent:
            await message.answer("Инвайт-ссылка предназначена для клиента. Вы уже в роли агента.")
            return
        ok, reason = await consume_agent_invite(
            token,
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
            await _show_client_menu(
                message,
                message.from_user.id,
                is_superadmin=is_superadmin,
                text=f"✅ Вы подключены к агенту: {agent_name}. Открываю меню клиента.",
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
            "invite_wrong_type": "Неверный тип приглашения.",
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
        await _show_agent_menu(
            message,
            is_superadmin=is_superadmin,
            text="Вы в роли агента.",
        )
        return
    if user.role == UserRole.client:
        await state.clear()
        await _show_client_menu(
            message,
            message.from_user.id,
            is_superadmin=is_superadmin,
            text="Вы в роли клиента.",
        )
        return

    await message.answer(
        "Здравствуйте! Кто вы?",
        reply_markup=role_keyboard(),
    )


@router.callback_query(F.data.in_({"role:agent", "role:client"}))
async def choose_role(callback: CallbackQuery, state: FSMContext, is_superadmin: bool = False) -> None:
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
            await _show_agent_menu(
                callback,
                is_superadmin=is_superadmin,
                text="Роль сохранена: агент.",
            )
    else:
        revoke_agent_session(callback.from_user.id)
        await state.clear()
        await _show_client_menu(
            callback,
            callback.from_user.id,
            is_superadmin=is_superadmin,
            text="Роль сохранена: клиент.",
        )

    await callback.answer()


@router.message(F.text == Btn.SWITCH_TO_CLIENT)
async def switch_to_client(message: Message, state: FSMContext, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        return
    if not get_settings().dev_role_switch_enabled:
        await message.answer("Переключение ролей отключено в этом окружении.")
        return
    user = await get_or_create_user(message.from_user.id)
    if user.role == UserRole.superadmin:
        await message.answer("Переключение в клиентский режим недоступно.")
        return
    await set_user_role(message.from_user.id, UserRole.client)
    revoke_agent_session(message.from_user.id)
    await state.clear()
    await _show_client_menu(
        message,
        message.from_user.id,
        is_superadmin=is_superadmin,
        text="Переключились в режим клиента.",
    )


@router.message(F.text == Btn.SWITCH_TO_AGENT)
async def switch_to_agent(message: Message, state: FSMContext, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        return
    if not get_settings().dev_role_switch_enabled:
        await message.answer("Переключение ролей отключено в этом окружении.")
        return
    user = await get_or_create_user(message.from_user.id)
    if user.role == UserRole.agent:
        authorize_agent_session(message.from_user.id)
        await state.clear()
        await _show_agent_menu(
            message,
            is_superadmin=is_superadmin,
            text="Вы уже в роли агента.",
        )
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
    await _show_agent_menu(
        message,
        is_superadmin=is_superadmin,
        text="Переключил роль: агент.",
    )


@router.message(AgentLogin.password)
async def agent_login_password(message: Message, state: FSMContext, is_superadmin: bool = False) -> None:
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
    sa = is_superadmin or _is_superadmin_user(message.from_user.id)
    await _show_agent_menu(
        message,
        is_superadmin=sa,
        text="Добро пожаловать!",
    )


@router.message(AgentProfileSetup.name)
async def agent_profile_name(message: Message, state: FSMContext, is_superadmin: bool = False) -> None:
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
    data = await state.get_data()
    reg_tok = data.get("agent_registration_invite_token")
    if reg_tok:
        await consume_agent_registration_invite(reg_tok, message.from_user.id)
        await state.update_data(agent_registration_invite_token=None)
    if await has_agent_password(message.from_user.id) and not is_agent_session_active(message.from_user.id):
        await state.set_state(AgentLogin.password)
        await message.answer("Имя сохранено. Теперь введите пароль агента:")
        return
    await state.clear()
    await _show_agent_menu(
        message,
        is_superadmin=is_superadmin,
        text=f"✅ Имя агента сохранено: {name}",
    )


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
async def cmd_login(message: Message, state: FSMContext, is_superadmin: bool = False) -> None:
    user = await get_or_create_user(message.from_user.id)
    if user.role != UserRole.agent:
        await message.answer("Команда доступна только агенту.")
        return
    if not await has_agent_password(message.from_user.id):
        await message.answer("Пароль агента не задан. Установите его в Настройки → Доступ агента.")
        return
    if is_agent_session_active(message.from_user.id):
        await _show_agent_menu(
            message,
            is_superadmin=is_superadmin,
            text="Сессия уже активна.",
        )
        return
    await state.set_state(AgentLogin.password)
    await message.answer("🔐 Введите пароль агента:")


@router.message(F.text == Btn.MAIN_MENU)
async def to_main_menu(message: Message, state: FSMContext, is_superadmin: bool = False) -> None:
    await _delete_user_button_message(message)
    if await state.get_state() is not None:
        await state.clear()
    user = await get_or_create_user(message.from_user.id)
    if user.role == UserRole.superadmin:
        await state.clear()
        await _show_superadmin_home(message, text="👑 Главное меню супер-админа.")
        return
    if user.role == UserRole.agent:
        if await has_agent_password(message.from_user.id) and not is_agent_session_active(message.from_user.id):
            await state.set_state(AgentLogin.password)
            await message.answer("🔐 Введите пароль агента:")
            return
        await state.clear()
        await _show_agent_menu(
            message,
            is_superadmin=is_superadmin,
            text="Выберите действие.",
        )
        return
    if user.role == UserRole.client:
        await state.clear()
        await _show_client_menu(
            message,
            message.from_user.id,
            is_superadmin=is_superadmin,
            text="Выберите действие.",
        )
        return
    await message.answer("Здравствуйте! Кто вы?", reply_markup=role_keyboard())


@router.callback_query(F.data == "dev:role:agent")
async def dev_switch_agent(callback: CallbackQuery, state: FSMContext, is_superadmin: bool = False) -> None:
    if not _is_superadmin_user(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    if not get_settings().dev_role_switch_enabled:
        await callback.answer("Dev-переключение выключено.", show_alert=True)
        return
    await state.update_data(dev_role="agent")
    await callback.answer()
    await _show_agent_menu(
        callback,
        is_superadmin=is_superadmin,
        text="💼 Режим агента (dev)",
    )


@router.callback_query(F.data == "dev:role:client")
async def dev_switch_client(callback: CallbackQuery, state: FSMContext, is_superadmin: bool = False) -> None:
    if not _is_superadmin_user(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    if not get_settings().dev_role_switch_enabled:
        await callback.answer("Dev-переключение выключено.", show_alert=True)
        return
    await state.update_data(dev_role="client")
    await callback.answer()
    await _show_client_menu(
        callback,
        callback.from_user.id,
        is_superadmin=is_superadmin,
        text="👤 Режим клиента (dev)",
    )


@router.message(F.text == Btn.BACK_TO_ADMIN)
async def back_to_admin_mode(message: Message, state: FSMContext) -> None:
    user = await get_or_create_user(message.from_user.id)
    if user.role != UserRole.superadmin:
        return
    await state.update_data(dev_role=None)
    await _show_superadmin_home(message, text="👑 Возврат в режим Admin.")
