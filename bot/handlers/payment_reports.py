from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest

from bot.config import get_settings
from bot.scheduler.payment_reminders import _payments_to_xlsx_bytes, get_pending_payments_for_due_date

router = Router()


@router.callback_query(F.data.startswith("payrep:"))
async def payrep_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return

    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    try:
        parts = callback.data.split(":")
        days = int(parts[1])
        if days not in (1, 3, 7):
            await callback.answer("Некорректные данные", show_alert=True)
            return
        page = int(parts[3]) if len(parts) >= 4 and parts[2] == "page" else 0
    except Exception:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    today = datetime.now(tz).date()
    due_date = today + timedelta(days=days)

    payments = await get_pending_payments_for_due_date(callback.from_user.id, due_date)
    if not payments:
        await callback.message.answer(
            f"⏳ Взносы через {days} дней: нет ожидающих платежей (дата: {due_date:%d.%m.%Y})."
        )
        await callback.answer()
        return

    fn, payload = _payments_to_xlsx_bytes(payments, days_ahead=days, due_date=due_date)
    try:
        from aiogram.types import BufferedInputFile

        await callback.message.answer_document(
            document=BufferedInputFile(payload, filename=fn),
            caption=f"⏳ Взносы через {days} день(ей) (дата: {due_date:%d.%m.%Y}). Всего: {len(payments)}",
        )
    except TelegramBadRequest:
        # Fallback: if something still goes wrong, just show a shortened text.
        await callback.message.answer(f"Не удалось отправить файл. Всего платежей: {len(payments)}")
    await callback.answer()

