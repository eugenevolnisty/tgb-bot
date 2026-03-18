from aiogram import F, Router
from aiogram.types import Message

from bot.db.models import UserRole
from bot.db.repo import get_or_create_user
from bot.keyboards import Btn, client_menu

router = Router()


async def _ensure_client(message: Message) -> bool:
    user = await get_or_create_user(message.from_user.id)
    return user.role == UserRole.client


@router.message(F.text == Btn.MY_CONTRACTS)
async def client_contracts(message: Message) -> None:
    if not await _ensure_client(message):
        return
    await message.answer("Раздел «Мои договоры» (заглушка).", reply_markup=client_menu())


@router.message(F.text == Btn.MY_DOCS)
async def client_documents(message: Message) -> None:
    if not await _ensure_client(message):
        return
    await message.answer("Раздел «Мои документы» (заглушка).", reply_markup=client_menu())
