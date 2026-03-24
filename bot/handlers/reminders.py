from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from bot.config import get_settings
from bot.db.models import UserRole
from bot.db.models import ReminderRepeat
from bot.db.repo import cancel_reminder, create_reminder, get_or_create_user, list_agent_reminders
from bot.db.repo import delete_reminder, update_reminder_datetime
from bot.keyboards import Btn, agent_menu
from bot.services.datetime_parse import combine_local, parse_date_ru, parse_duration_ru, parse_relative_ru, parse_time_ru

router = Router()


class RemindersMenu:
    MY = "📋 Мои напоминания"
    CREATE = "➕ Создать напоминание"
    BACK = "⬅️ Назад"


def reminders_menu_keyboard() -> "aiogram.types.ReplyKeyboardMarkup":
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=RemindersMenu.MY)],
            [KeyboardButton(text=RemindersMenu.CREATE)],
            [KeyboardButton(text=RemindersMenu.BACK)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Напоминания",
    )


class ReminderCreate(StatesGroup):
    text = State()
    date = State()
    time = State()
    confirm = State()
    repeat = State()


class ReminderSnooze(StatesGroup):
    custom = State()


class ReminderEdit(StatesGroup):
    date = State()
    time = State()
    confirm = State()


async def _ensure_agent(message: Message) -> bool:
    user = await get_or_create_user(message.from_user.id)
    return user.role == UserRole.agent


async def _ensure_agent_tg(tg_id: int) -> bool:
    user = await get_or_create_user(tg_id)
    return user.role == UserRole.agent


@router.message(F.text == Btn.REMINDERS)
async def open_reminders(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    await message.answer("⏰ Напоминания:", reply_markup=reminders_menu_keyboard())


@router.message(F.text == RemindersMenu.BACK)
async def reminders_back(message: Message, state: FSMContext) -> None:
    if await state.get_state() is not None:
        await state.clear()
    await message.answer("Меню.", reply_markup=agent_menu())


@router.message(F.text == RemindersMenu.MY)
async def my_reminders(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    items = await list_agent_reminders(message.from_user.id, limit=10)
    if not items:
        await message.answer("Напоминаний пока нет.", reply_markup=reminders_menu_keyboard())
        return
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    await message.answer("📋 Последние напоминания:", reply_markup=reminders_menu_keyboard())
    for r in items:
        local_dt = r.remind_at.astimezone(tz)
        rep = {
            ReminderRepeat.none: "—",
            ReminderRepeat.daily: "каждый день",
            ReminderRepeat.weekly: "каждую неделю",
            ReminderRepeat.monthly: "каждый месяц",
        }.get(r.repeat, "—")
        text = (
            f"#{r.id} • {local_dt:%d.%m.%Y %H:%M}\n"
            f"Статус: {r.status.value} • Повтор: {rep}\n"
            f"✍️ {r.text}"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🕒 Изменить дату/время", callback_data=f"rem:edit:{r.id}"),
                    InlineKeyboardButton(text="🗑 Удалить", callback_data=f"rem:delete:{r.id}"),
                ]
            ]
        )
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("rem:delete:"))
async def delete_from_list(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        reminder_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    await delete_reminder(reminder_id)
    await callback.answer("Удалено")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@router.callback_query(F.data.startswith("rem:edit:"))
async def edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        reminder_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return

    await state.clear()
    await state.update_data(edit_reminder_id=reminder_id)
    await state.set_state(ReminderEdit.date)
    await callback.message.answer("Новая дата? (завтра / в понедельник / 20.03.2026). Можно “отмена”.", reply_markup=ReplyKeyboardRemove())
    await callback.answer()


@router.message(ReminderEdit.date)
async def edit_step_date(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz)
    res = parse_date_ru(message.text or "", today=now_local.date())
    if res is None:
        await message.answer("Не понял дату. Пример: завтра / в понедельник / 20.03.2026")
        return
    await state.update_data(edit_date_iso=res.target_date.isoformat())
    await state.set_state(ReminderEdit.time)
    await message.answer(f"Новое время? Примеры: в 16:40 / в 5 вечера / через час. Дата: {res.target_date:%d.%m.%Y}.")


@router.message(ReminderEdit.time)
async def edit_step_time(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz)
    data = await state.get_data()

    rel = parse_relative_ru(message.text or "", now_local=now_local)
    if rel is not None:
        remind_local = rel
    else:
        t = parse_time_ru(message.text or "")
        if t is None:
            await message.answer("Не понял время. Примеры: 14:30 / в 16 40 / в 5 вечера / через час / через 2 минуты")
            return
        d = datetime.fromisoformat(data["edit_date_iso"]).date()
        remind_local = combine_local(d, t, now_local=now_local)

    await state.update_data(edit_remind_local=remind_local.isoformat())
    await state.set_state(ReminderEdit.confirm)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Сохранить", callback_data="rem:edit_confirm:yes"),
                InlineKeyboardButton(text="✏️ Изменить ещё раз", callback_data="rem:edit_confirm:no"),
            ]
        ]
    )
    await message.answer(
        f"Проверим новое время:\n🗓 {remind_local:%d.%m.%Y}  🕒 {remind_local:%H:%M}\nВерно?",
        reply_markup=kb,
    )


@router.callback_query(F.data.in_({"rem:edit_confirm:yes", "rem:edit_confirm:no"}))
async def edit_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    if callback.data.endswith(":no"):
        await state.set_state(ReminderEdit.date)
        await callback.message.answer("Ок, введи новую дату ещё раз.")
        await callback.answer()
        return

    data = await state.get_data()
    reminder_id = int(data["edit_reminder_id"])
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    remind_local = datetime.fromisoformat(data["edit_remind_local"])
    if remind_local.tzinfo is None:
        remind_local = remind_local.replace(tzinfo=tz)
    remind_utc = remind_local.astimezone(timezone.utc)
    await update_reminder_datetime(reminder_id, remind_utc)
    await state.clear()
    await callback.message.answer(f"✅ Обновил напоминание #{reminder_id} на {remind_local:%d.%m.%Y %H:%M}.", reply_markup=reminders_menu_keyboard())
    await callback.answer()


@router.message(F.text == RemindersMenu.CREATE)
async def start_create_reminder(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    await state.clear()
    await state.set_state(ReminderCreate.text)
    await message.answer("О чём напомнить? (текст). Можно написать “отмена”.", reply_markup=ReplyKeyboardRemove())


@router.message(F.text.casefold() == "отмена")
async def cancel_any(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        return
    await state.clear()
    await message.answer("Ок, отменил.", reply_markup=reminders_menu_keyboard())


@router.message(ReminderCreate.text)
async def step_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("Слишком коротко. Опиши напоминание чуть подробнее.")
        return
    await state.update_data(text=text)
    await state.set_state(ReminderCreate.date)
    await message.answer("Какая дата? (например: завтра / в понедельник / 20.03.2026)")


@router.message(ReminderCreate.date)
async def step_date(message: Message, state: FSMContext) -> None:
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz)
    res = parse_date_ru(message.text or "", today=now_local.date())
    if res is None:
        await message.answer("Не понял дату. Пример: завтра / в понедельник / 20.03.2026")
        return
    await state.update_data(date_iso=res.target_date.isoformat(), date_norm=res.normalized)
    await state.set_state(ReminderCreate.time)
    await message.answer(f"В какое время? (например: 14:30). Дата распознана как {res.target_date:%d.%m.%Y}.")


@router.message(ReminderCreate.time)
async def step_time(message: Message, state: FSMContext) -> None:
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz)
    data = await state.get_data()
    # Relative time: "через час", "через 2 минуты", ...
    rel = parse_relative_ru(message.text or "", now_local=now_local)
    if rel is not None:
        remind_local = rel
        t = remind_local.timetz().replace(tzinfo=None)
        await state.update_data(date_iso=remind_local.date().isoformat(), date_norm="относительно", time_hm=f"{t.hour:02d}:{t.minute:02d}")
    else:
        t = parse_time_ru(message.text or "")
        if t is None:
            await message.answer("Не понял время. Примеры: 14:30 / в 16 40 / в 5 вечера / через час / через 2 минуты")
            return
        d = datetime.fromisoformat(data["date_iso"]).date()
        remind_local = combine_local(d, t, now_local=now_local)
        await state.update_data(time_hm=f"{t.hour:02d}:{t.minute:02d}")

    await state.update_data(remind_local=remind_local.isoformat())
    await state.set_state(ReminderCreate.confirm)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, сохранить", callback_data="rem:confirm:yes"),
                InlineKeyboardButton(text="✏️ Нет, изменить", callback_data="rem:confirm:no"),
            ]
        ]
    )
    await message.answer(
        f"Проверим:\n"
        f"🗓 Дата: {remind_local:%d.%m.%Y}\n"
        f"🕒 Время: {remind_local:%H:%M}\n"
        f"Это верно?",
        reply_markup=kb,
    )


@router.callback_query(F.data.in_({"rem:confirm:yes", "rem:confirm:no"}))
async def confirm_reminder(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return

    if callback.data.endswith(":no"):
        await state.set_state(ReminderCreate.date)
        await callback.message.answer("Ок, давай заново дату. (завтра / в понедельник / 20.03.2026)")
        await callback.answer()
        return

    data = await state.get_data()
    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    remind_local = datetime.fromisoformat(data["remind_local"])
    if remind_local.tzinfo is None:
        remind_local = remind_local.replace(tzinfo=tz)
    remind_utc = remind_local.astimezone(timezone.utc)

    await state.update_data(remind_utc=remind_utc.isoformat(), remind_local_out=remind_local.isoformat())
    await state.set_state(ReminderCreate.repeat)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Без повтора", callback_data="rem:repeat:none")],
            [
                InlineKeyboardButton(text="Каждый день", callback_data="rem:repeat:daily"),
                InlineKeyboardButton(text="Каждую неделю", callback_data="rem:repeat:weekly"),
            ],
            [InlineKeyboardButton(text="Каждый месяц", callback_data="rem:repeat:monthly")],
        ]
    )
    await callback.message.answer("Сделать напоминание повторяющимся?", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("rem:repeat:"))
async def pick_repeat(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    data = await state.get_data()
    if "remind_utc" not in data or "text" not in data:
        await callback.answer("Сессия устарела", show_alert=True)
        return
    repeat_key = callback.data.split(":", 2)[2]
    repeat = ReminderRepeat.none
    if repeat_key == "daily":
        repeat = ReminderRepeat.daily
    elif repeat_key == "weekly":
        repeat = ReminderRepeat.weekly
    elif repeat_key == "monthly":
        repeat = ReminderRepeat.monthly

    remind_utc = datetime.fromisoformat(data["remind_utc"])
    r = await create_reminder(callback.from_user.id, text_value=str(data["text"]), remind_at_utc=remind_utc, repeat=repeat)
    remind_local = datetime.fromisoformat(data["remind_local_out"])
    await state.clear()
    rep_txt = {
        ReminderRepeat.none: "без повтора",
        ReminderRepeat.daily: "каждый день",
        ReminderRepeat.weekly: "каждую неделю",
        ReminderRepeat.monthly: "каждый месяц",
    }[repeat]
    await callback.message.answer(
        f"✅ Напоминание сохранено (#{r.id}) на {remind_local:%d.%m.%Y %H:%M} ({rep_txt}).",
        reply_markup=reminders_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rem:snooze:"))
async def snooze_quick(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        _, _, reminder_id_s, minutes_s = callback.data.split(":", 3)
        reminder_id = int(reminder_id_s)
        minutes = int(minutes_s)
    except Exception:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    # Extract reminder text from message, simplest: last line after "✍️ "
    txt = ""
    if callback.message.text:
        for line in callback.message.text.splitlines():
            if line.startswith("✍️ "):
                txt = line.removeprefix("✍️ ").strip()
                break
    if not txt:
        txt = "Напоминание"

    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz)
    remind_local = now_local.replace(second=0, microsecond=0) + timedelta(minutes=minutes)
    r = await create_reminder(callback.from_user.id, text_value=txt, remind_at_utc=remind_local.astimezone(timezone.utc))
    await callback.answer(f"Ок, напомню через {minutes} мин (#{r.id})")


@router.callback_query(F.data.startswith("rem:snooze_other:"))
async def snooze_other_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        reminder_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    txt = ""
    if callback.message.text:
        for line in callback.message.text.splitlines():
            if line.startswith("✍️ "):
                txt = line.removeprefix("✍️ ").strip()
                break
    if not txt:
        txt = "Напоминание"

    await state.clear()
    await state.set_state(ReminderSnooze.custom)
    await state.update_data(snooze_text=txt, snooze_from_id=reminder_id)
    await callback.message.answer("Через сколько напомнить ещё раз? Примеры: 5 минут / 2 часа / 1ч 30м / через час")
    await callback.answer()


@router.message(ReminderSnooze.custom)
async def snooze_other_finish(message: Message, state: FSMContext) -> None:
    if not await _ensure_agent(message):
        return
    data = await state.get_data()
    txt = str(data.get("snooze_text") or "Напоминание")

    settings = get_settings()
    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz)

    dur = parse_duration_ru(message.text or "")
    if dur is None:
        await message.answer("Не понял. Примеры: 5 минут / 2 часа / 1 час 7 минут / 1ч 30м")
        return
    remind_local = now_local.replace(second=0, microsecond=0) + dur
    r = await create_reminder(message.from_user.id, text_value=txt, remind_at_utc=remind_local.astimezone(timezone.utc))
    await state.clear()
    await message.answer(f"✅ Ок! Напомню ещё раз в {remind_local:%d.%m.%Y %H:%M} (#{r.id}).", reply_markup=reminders_menu_keyboard())


@router.callback_query(F.data.startswith("rem:cancel:"))
async def cancel_from_message(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not await _ensure_agent_tg(callback.from_user.id):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        reminder_id = int(callback.data.split(":", 2)[2])
    except Exception:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    await cancel_reminder(reminder_id)
    await callback.answer("Отменено")

