from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import get_settings
from bot.db.models import ReminderRepeat
from bot.db.repo import fetch_due_reminders, mark_reminder_sent, reschedule_recurring_reminder

log = logging.getLogger(__name__)


async def reminders_worker(bot: Bot, poll_interval_seconds: int = 30) -> None:
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        log.warning("Timezone %r not found, falling back to UTC. Install tzdata on Windows.", settings.timezone)
        tz = ZoneInfo("UTC")
    while True:
        try:
            due = await fetch_due_reminders(limit=50)
            for reminder, agent_tg_id in due:
                local_dt = reminder.remind_at.astimezone(tz)
                text = (
                    "⏰ Напоминание\n"
                    f"🗓 {local_dt:%d.%m.%Y %H:%M}\n"
                    f"✍️ {reminder.text}"
                )
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text="⏱ +5 мин", callback_data=f"rem:snooze:{reminder.id}:5"),
                            InlineKeyboardButton(text="⏱ +10 мин", callback_data=f"rem:snooze:{reminder.id}:10"),
                            InlineKeyboardButton(text="⏱ +30 мин", callback_data=f"rem:snooze:{reminder.id}:30"),
                        ],
                        [
                            InlineKeyboardButton(text="⏱ +1 час", callback_data=f"rem:snooze:{reminder.id}:60"),
                            InlineKeyboardButton(text="📝 Другое", callback_data=f"rem:snooze_other:{reminder.id}"),
                            InlineKeyboardButton(text="🔕 Отменить", callback_data=f"rem:cancel:{reminder.id}"),
                        ],
                    ]
                )
                await bot.send_message(agent_tg_id, text, reply_markup=kb)
                if getattr(reminder, "repeat", ReminderRepeat.none) != ReminderRepeat.none:
                    next_dt = reminder.remind_at
                    if reminder.repeat == ReminderRepeat.daily:
                        next_dt = next_dt + timedelta(days=1)
                    elif reminder.repeat == ReminderRepeat.weekly:
                        next_dt = next_dt + timedelta(days=7)
                    elif reminder.repeat == ReminderRepeat.monthly:
                        # add 1 month, clamp day
                        y = next_dt.year
                        m = next_dt.month + 1
                        if m == 13:
                            y += 1
                            m = 1
                        d = next_dt.day
                        # days in month
                        import calendar

                        last = calendar.monthrange(y, m)[1]
                        d = min(d, last)
                        next_dt = next_dt.replace(year=y, month=m, day=d)
                    await reschedule_recurring_reminder(reminder.id, next_dt.astimezone(timezone.utc))
                else:
                    await mark_reminder_sent(reminder.id)
        except Exception as e:
            log.exception("Reminder worker error: %s", e)

        await asyncio.sleep(poll_interval_seconds)

