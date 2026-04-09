from aiogram import F, Router
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import get_settings
from bot.db.repo import (
    block_agent,
    count_clients_by_agent,
    create_superadmin_invite,
    get_all_agents,
    reset_test_agents,
    reset_test_clients,
)

router = Router()


class IsSuperAdmin(Filter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id == get_settings().superadmin_tg_id


def _is_superadmin_user(user_id: int) -> bool:
    settings = get_settings()
    return bool(settings.superadmin_tg_id) and user_id == settings.superadmin_tg_id


def _sa_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Список агентов", callback_data="sa:agents")],
            [InlineKeyboardButton(text="🔑 Создать инвайт для агента", callback_data="sa:invite_new")],
            [InlineKeyboardButton(text="🗑 Сбросить тестовых клиентов", callback_data="sa:reset_clients")],
            [InlineKeyboardButton(text="🗑 Сбросить тестовых агентов", callback_data="sa:reset_agents")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="sa:main_menu")],
        ]
    )


def _sa_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="← Назад", callback_data="sa:menu")],
        ]
    )


@router.message(Command("admin"), IsSuperAdmin())
async def sa_panel(message: Message):
    agents = await get_all_agents()
    text = f"👑 <b>Супер-Админ</b>\n\nАгентов: {len(agents)}"
    await message.answer(text, reply_markup=_sa_menu_kb())


@router.callback_query(F.data == "sa:agents")
async def sa_agents(callback: CallbackQuery):
    if not _is_superadmin_user(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await callback.answer()
    agents = await get_all_agents()
    if not agents:
        await callback.message.edit_text("👥 Агентов пока нет.", reply_markup=_sa_back_kb())
        return

    text = "👥 <b>Агенты:</b>\n\n"
    buttons: list[list[InlineKeyboardButton]] = []
    for agent in agents:
        count = await count_clients_by_agent(agent.id)
        name = agent.display_name or f"tg:{agent.tg_id}"
        text += (
            f"• <b>{name}</b>\n"
            f"  ID: {agent.tg_id} | "
            f"Клиентов: {count}\n"
            f"  С: {agent.created_at:%d.%m.%Y}\n\n"
        )
        buttons.append([InlineKeyboardButton(text=f"⚙️ {name}", callback_data=f"sa:agent:{agent.tg_id}")])

    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="sa:menu")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("sa:agent:"))
async def sa_agent_card(callback: CallbackQuery):
    if not _is_superadmin_user(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await callback.answer()
    agent_tg_id = int(callback.data.split(":")[2])
    agents = await get_all_agents()
    agent = next((a for a in agents if a.tg_id == agent_tg_id), None)
    if not agent:
        await callback.message.edit_text("Агент не найден.")
        return

    count = await count_clients_by_agent(agent.id)
    name = agent.display_name or f"tg:{agent.tg_id}"
    text = (
        f"👤 <b>{name}</b>\n\n"
        f"tg_id: {agent.tg_id}\n"
        f"Клиентов: {count}\n"
        f"Создан: {agent.created_at:%d.%m.%Y}\n"
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"sa:block:{agent_tg_id}")],
                [InlineKeyboardButton(text="← К списку", callback_data="sa:agents")],
            ]
        ),
    )


@router.callback_query(F.data == "sa:invite_new")
async def sa_create_invite_new(callback: CallbackQuery):
    if not _is_superadmin_user(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await callback.answer()
    settings = get_settings()
    invite = await create_superadmin_invite(settings.superadmin_tg_id)
    if not invite:
        await callback.message.edit_text("❌ Не удалось создать инвайт.", reply_markup=_sa_back_kb())
        return

    bot_info = await callback.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=inv_{invite.token}"
    await callback.message.edit_text(
        "🔑 <b>Инвайт для агента создан</b>\n\n"
        "Ссылка (действует 7 дней):\n"
        f"<code>{link}</code>\n\n"
        "Или код:\n"
        f"<code>{invite.token}</code>\n\n"
        "Отправь эту ссылку агенту.",
        reply_markup=_sa_back_kb(),
    )


@router.callback_query(F.data.startswith("sa:block:"))
async def sa_block_agent(callback: CallbackQuery):
    if not _is_superadmin_user(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await callback.answer()
    agent_tg_id = int(callback.data.split(":")[2])
    success = await block_agent(agent_tg_id)
    text = "✅ Агент заблокирован." if success else "❌ Не удалось заблокировать."
    await callback.message.edit_text(text, reply_markup=_sa_back_kb())


@router.callback_query(F.data == "sa:reset_clients")
async def sa_reset_clients_confirm(callback: CallbackQuery):
    if not _is_superadmin_user(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        "⚠️ <b>Сброс тестовых клиентов</b>\n\n"
        "Это действие:\n"
        "• Сбросит tenant_id всех клиентов на дефолтный\n"
        "• Удалит их из CRM-таблицы клиентов (source_user_id)\n"
        "• НЕ удалит их аккаунты и историю\n\n"
        "Они смогут заново привязаться по ссылке агента.\n\n"
        "Продолжить?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, сбросить клиентов", callback_data="sa:reset_clients_ok")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="sa:menu")],
            ]
        ),
    )


@router.callback_query(F.data == "sa:reset_clients_ok")
async def sa_reset_clients_do(callback: CallbackQuery):
    if not _is_superadmin_user(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await callback.answer()
    count = await reset_test_clients()
    await callback.message.edit_text(
        f"✅ Сброшено клиентов: {count}\n\n"
        f"Они могут заново привязаться по ссылке агента.",
        reply_markup=_sa_back_kb(),
    )


@router.callback_query(F.data == "sa:reset_agents")
async def sa_reset_agents_confirm(callback: CallbackQuery):
    if not _is_superadmin_user(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        "⚠️ <b>Сброс тестовых агентов</b>\n\n"
        "Это действие удалит для каждого агента (кроме суперадмина):\n"
        "• Роль агента (role → None)\n"
        "• Персональный тенант\n"
        "• Всех CRM-клиентов агента\n"
        "• Все договоры и платежи\n"
        "• Все инвайты агента\n"
        "• Пароль агента (AgentCredential)\n\n"
        "⛔ Это необратимо!\n\n"
        "Продолжить?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, сбросить агентов", callback_data="sa:reset_agents_ok")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="sa:menu")],
            ]
        ),
    )


@router.callback_query(F.data == "sa:reset_agents_ok")
async def sa_reset_agents_do(callback: CallbackQuery):
    if not _is_superadmin_user(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await callback.answer()
    settings = get_settings()
    count = await reset_test_agents(exclude_tg_id=settings.superadmin_tg_id)
    await callback.message.edit_text(
        f"✅ Сброшено агентов: {count}\n\n"
        f"Их аккаунты Telegram сохранены.\n"
        f"Они могут заново зарегистрироваться по инвайт-ссылке суперадмина.",
        reply_markup=_sa_back_kb(),
    )


@router.callback_query(F.data == "sa:menu")
async def sa_back_menu(callback: CallbackQuery):
    if not _is_superadmin_user(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await callback.answer()
    agents = await get_all_agents()
    await callback.message.edit_text(
        f"👑 <b>Супер-Админ</b>\n\nАгентов: {len(agents)}",
        reply_markup=_sa_menu_kb(),
    )


@router.callback_query(F.data == "sa:main_menu")
async def sa_to_main_menu(callback: CallbackQuery):
    if not _is_superadmin_user(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    rows = [[InlineKeyboardButton(text="👑 Админ-панель", callback_data="sa:menu")]]
    if get_settings().dev_role_switch_enabled:
        rows.append([InlineKeyboardButton(text="💼 Режим агента", callback_data="dev:role:agent")])
        rows.append([InlineKeyboardButton(text="👤 Режим клиента", callback_data="dev:role:client")])
    await callback.message.answer(
        "Выберите режим:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.message()
async def sa_fsm_passthrough(
    message: Message,
    state: FSMContext,
) -> None:
    from aiogram.dispatcher.event.bases import UNHANDLED
    from bot.keyboards import Btn

    text = (message.text or "").strip()

    # 1. Команды — всегда пропускать
    if text.startswith("/"):
        return UNHANDLED

    # 2. Кнопки агентского/клиентского меню
    #    — всегда пропускать
    agent_buttons = {
        Btn.INCOMING,
        Btn.IN_PROGRESS,
        Btn.MY_CLIENTS,
        Btn.DASHBOARD,
        Btn.REMINDERS,
        Btn.REPORTS,
        Btn.SETTINGS,
        Btn.ADD_PAYMENT,
        Btn.SWITCH_TO_CLIENT,
        Btn.SWITCH_TO_AGENT,
        Btn.BACK_TO_ADMIN,
        Btn.MAIN_MENU,
        Btn.CALC_PRICE,
        Btn.LEAVE_APP,
        Btn.MY_CONTRACTS,
        Btn.MY_DOCS,
        Btn.CONTACT_AGENT,
        Btn.NEXT_PAYMENT,
        Btn.ROLE_AGENT,
        Btn.ROLE_CLIENT,
    }
    if text in agent_buttons:
        return UNHANDLED

    # 3. Если есть активное FSM состояние
    #    — пропускать (FSM хендлер обработает)
    current_state = await state.get_state()
    if current_state is not None:
        return UNHANDLED

    # 4. Всё остальное без FSM —
    #    неизвестный текст от суперадмина
    #    просто игнорируем (не отвечаем)
    return UNHANDLED
