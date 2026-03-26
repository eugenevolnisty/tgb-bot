from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from bot.config import get_settings
from bot.scheduler.payment_reminders import (
    _contract_ends_period_to_xlsx_bytes,
    get_contracts_ending_between,
)

router = Router()


@router.callback_query(F.data.startswith("endrep:"))
async def endrep_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return

    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    parts = callback.data.split(":")
    today = datetime.now(tz).date()

    try:
        days = int(parts[1])
        if days not in (1, 3, 7, 30):
            await callback.answer("Некорректные данные", show_alert=True)
            return
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные", show_alert=True)
        return

    end = today + timedelta(days=days)
    rows = await get_contracts_ending_between(callback.from_user.id, today, end)
    if not rows:
        await callback.message.answer(
            f"📅 Заканчивающиеся договоры за период {today:%d.%m.%Y}—{end:%d.%m.%Y}: нет."
        )
        await callback.answer()
        return

    fn, payload = _contract_ends_period_to_xlsx_bytes(rows, date_from=today, date_to=end)
    caption = f"📅 Заканчивающиеся договоры за период {today:%d.%m.%Y}—{end:%d.%m.%Y}. Всего: {len(rows)}"
    try:
        from aiogram.types import BufferedInputFile

        await callback.message.answer_document(
            document=BufferedInputFile(payload, filename=fn),
            caption=caption,
        )
    except TelegramBadRequest:
        await callback.message.answer(f"Не удалось отправить файл. Всего договоров: {len(rows)}")
    await callback.answer()
