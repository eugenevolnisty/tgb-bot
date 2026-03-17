from aiogram import F, Router
from aiogram.types import Message

from bot.db.models import UserRole
from bot.db.repo import get_or_create_user, list_incoming_applications
from bot.keyboards import agent_menu

router = Router()


async def _ensure_agent(message: Message) -> bool:
    user = await get_or_create_user(message.from_user.id)
    return user.role == UserRole.agent


@router.message(F.text == "Входящие заявки")
async def agent_incoming(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    apps = await list_incoming_applications()
    if not apps:
        await message.answer("Новых заявок нет.", reply_markup=agent_menu())
        return
    lines = [f"Заявка №{a.id} от клиента user_id={a.client_user_id} (статус: {a.status.value})" for a in apps]
    await message.answer("\n".join(lines), reply_markup=agent_menu())


@router.message(F.text == "Мои клиенты")
async def agent_clients(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    await message.answer("Раздел «Мои клиенты» (заглушка).", reply_markup=agent_menu())


@router.message(F.text == "Напоминания")
async def agent_reminders(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    await message.answer("Раздел «Напоминания» (заглушка).", reply_markup=agent_menu())


@router.message(F.text == "Отчёты")
async def agent_reports(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    await message.answer("Раздел «Отчёты» (заглушка).", reply_markup=agent_menu())


@router.message(F.text == "Настройки")
async def agent_settings(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    await message.answer("Раздел «Настройки» (заглушка).", reply_markup=agent_menu())
