from __future__ import annotations

from datetime import datetime, timedelta
from collections import defaultdict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from bot.config import get_settings
from bot.scheduler.payment_reminders import _payments_period_to_xlsx_bytes, get_pending_payments_due_between

router = Router()


def _payments_totals_by_currency(payments) -> str:
    totals_minor: dict[str, int] = defaultdict(int)
    for p in payments:
        totals_minor[str(p.currency)] += int(p.amount_minor)
    parts: list[str] = []
    for currency in sorted(totals_minor.keys()):
        amount = totals_minor[currency] / 100.0
        parts.append(f"{amount:.2f} {currency}")
    return ", ".join(parts)


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

    parts = callback.data.split(":")
    today = datetime.now(tz).date()

    try:
        if len(parts) >= 3 and parts[1] == "range":
            span = int(parts[2])
            if span not in (7, 30):
                await callback.answer("Некорректные данные", show_alert=True)
                return
            end = today + timedelta(days=span - 1)
            payments = await get_pending_payments_due_between(callback.from_user.id, today, end)
            if not payments:
                await callback.message.answer(
                    f"📅 Ожидающие взносы за период {today:%d.%m.%Y}—{end:%d.%m.%Y}: нет."
                )
                await callback.answer()
                return
            fn, payload = _payments_period_to_xlsx_bytes(payments, date_from=today, date_to=end)
            totals_txt = _payments_totals_by_currency(payments)
            cap = (
                f"📅 Ожидающие взносы за период {today:%d.%m.%Y}—{end:%d.%m.%Y}. "
                f"Всего: {len(payments)} взносов, {totals_txt}"
            )
        else:
            days = int(parts[1])
            if days not in (1, 3, 7):
                await callback.answer("Некорректные данные", show_alert=True)
                return
            end = today + timedelta(days=days)
            payments = await get_pending_payments_due_between(callback.from_user.id, today, end)
            if not payments:
                await callback.message.answer(
                    f"⏳ Ожидающие взносы за период {today:%d.%m.%Y}—{end:%d.%m.%Y}: нет."
                )
                await callback.answer()
                return
            fn, payload = _payments_period_to_xlsx_bytes(payments, date_from=today, date_to=end)
            totals_txt = _payments_totals_by_currency(payments)
            cap = (
                f"⏳ Ожидающие взносы за период {today:%d.%m.%Y}—{end:%d.%m.%Y}. "
                f"Всего: {len(payments)} взносов, {totals_txt}"
            )
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные", show_alert=True)
        return

    try:
        from aiogram.types import BufferedInputFile

        await callback.message.answer_document(
            document=BufferedInputFile(payload, filename=fn),
            caption=cap,
        )
    except TelegramBadRequest:
        await callback.message.answer(f"Не удалось отправить файл. Всего платежей: {len(payments)}")
    await callback.answer()

