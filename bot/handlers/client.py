from aiogram import F, Router
from aiogram.types import Message

from bot.db.models import UserRole
from bot.db.repo import create_application_for_client, get_or_create_user
from bot.keyboards import client_menu

router = Router()


async def _ensure_client(message: Message) -> bool:
    user = await get_or_create_user(message.from_user.id)
    return user.role == UserRole.client


@router.message(F.text == "Консультация")
async def client_consultation(message: Message) -> None:
    if not await _ensure_client(message):
        return
    await message.answer("Опишите ваш вопрос одним сообщением.", reply_markup=client_menu())


@router.message(F.text == "Рассчитать стоимость")
async def client_calc_price(message: Message) -> None:
    if not await _ensure_client(message):
        return
    await message.answer("Уточните параметры — я подготовлю расчёт (заглушка).", reply_markup=client_menu())


@router.message(F.text == "Оставить заявку")
async def client_leave_application(message: Message) -> None:
    if not await _ensure_client(message):
        return
    app = await create_application_for_client(message.from_user.id)
    await message.answer(f"Заявка №{app.id} создана и отправлена агенту.", reply_markup=client_menu())


@router.message(F.text == "Мои договоры")
async def client_contracts(message: Message) -> None:
    if not await _ensure_client(message):
        return
    await message.answer("Раздел «Мои договоры» (заглушка).", reply_markup=client_menu())


@router.message(F.text == "Мои документы")
async def client_documents(message: Message) -> None:
    if not await _ensure_client(message):
        return
    await message.answer("Раздел «Мои документы» (заглушка).", reply_markup=client_menu())
