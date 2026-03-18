import json

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.db.models import ApplicationStatus, UserRole
from bot.db.repo import get_or_create_user, list_incoming_applications, list_in_progress_applications, set_application_status
from bot.keyboards import Btn, agent_menu, application_actions_keyboard

router = Router()


async def _ensure_agent(message: Message) -> bool:
    user = await get_or_create_user(message.from_user.id)
    return user.role == UserRole.agent


async def _ensure_agent_tg(tg_id: int) -> bool:
    user = await get_or_create_user(tg_id)
    return user.role == UserRole.agent


@router.message(F.text == Btn.INCOMING)
async def agent_incoming(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    apps = await list_incoming_applications()
    if not apps:
        await message.answer("Новых заявок нет.", reply_markup=agent_menu())
        return
    for a in apps:
        client_tg = a.client.tg_id if a.client is not None else "?"
        header = f"📌 Заявка №{a.id}\n👤 Клиент: tg_id={client_tg}\n📎 Статус: {a.status.value}"

        details = ""
        if a.quote is not None:
            premium = a.quote.premium_amount / 100.0
            try:
                payload = json.loads(a.quote.input_json)
            except Exception:
                payload = {}
            if a.quote.quote_type.value == "kasko":
                name = payload.get("full_name")
                contact = payload.get("contact")
                bm = payload.get("brand_model", "?")
                year = payload.get("year", "?")
                car_value = payload.get("car_value", "?")
                abroad = "да" if payload.get("abroad") else "нет"
                drivers = payload.get("drivers_count", "?")
                age = payload.get("youngest_driver_age", "?")
                details = (
                    "\n🧮 КАСКО расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Авто: {bm} ({year})\n"
                    + f"- Стоимость: {car_value} BYN\n"
                    + f"- Заграница: {abroad}\n"
                    + f"- Водителей: {drivers}, мин. возраст: {age}\n"
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            elif a.quote.quote_type.value == "property":
                name = payload.get("full_name")
                contact = payload.get("contact")
                subject = payload.get("subject", "?")
                loc = payload.get("address_or_city", "?")
                value = payload.get("property_value", "?")
                comment = payload.get("comment")
                details = (
                    "\n🧮 Имущество расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Что страхуем: {subject}\n"
                    + f"- Локация: {loc}\n"
                    + f"- Стоимость: {value} BYN\n"
                    + (f"- Комментарий: {comment}\n" if comment else "")
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            elif a.quote.quote_type.value in {"cargo", "accident", "cmr", "dms", "other"}:
                name = payload.get("full_name")
                contact = payload.get("contact")
                subject = payload.get("subject", "?")
                value = payload.get("insured_value", "?")
                comment = payload.get("comment")
                extra = payload.get("extra_type")
                kind_map = {
                    "cargo": "📦 Грузы",
                    "accident": "🩹 Несчастные случаи",
                    "cmr": "🚚 CMR",
                    "dms": "🩺 ДМС",
                    "other": "✍️ Другой вид",
                }
                kind_title = kind_map.get(a.quote.quote_type.value, "✍️ Другой вид")
                if a.quote.quote_type.value == "other" and extra:
                    kind_title = f"{kind_title} ({extra})"
                details = (
                    f"\n🧮 {kind_title} расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Что страхуем: {subject}\n"
                    + f"- Стоимость: {value} BYN\n"
                    + (f"- Комментарий: {comment}\n" if comment else "")
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            else:
                details = f"\n🧮 Расчёт: {premium:.2f} {a.quote.currency}\n- Quote ID: {a.quote.id}"

        desc = f"\n📝 Комментарий: {a.description}" if a.description else ""
        await message.answer(header + details + desc, reply_markup=application_actions_keyboard(a.id))

    await message.answer("Меню.", reply_markup=agent_menu())


@router.message(F.text == Btn.IN_PROGRESS)
async def agent_in_progress(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    apps = await list_in_progress_applications()
    if not apps:
        await message.answer("Заявок в работе нет.", reply_markup=agent_menu())
        return
    for a in apps:
        client_tg = a.client.tg_id if a.client is not None else "?"
        header = f"🛠 В работе №{a.id}\n👤 Клиент: tg_id={client_tg}\n📎 Статус: {a.status.value}"

        details = ""
        if a.quote is not None:
            premium = a.quote.premium_amount / 100.0
            try:
                payload = json.loads(a.quote.input_json)
            except Exception:
                payload = {}
            if a.quote.quote_type.value == "kasko":
                name = payload.get("full_name")
                contact = payload.get("contact")
                bm = payload.get("brand_model", "?")
                year = payload.get("year", "?")
                car_value = payload.get("car_value", "?")
                abroad = "да" if payload.get("abroad") else "нет"
                drivers = payload.get("drivers_count", "?")
                age = payload.get("youngest_driver_age", "?")
                details = (
                    "\n🧮 КАСКО расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Авто: {bm} ({year})\n"
                    + f"- Стоимость: {car_value} BYN\n"
                    + f"- Заграница: {abroad}\n"
                    + f"- Водителей: {drivers}, мин. возраст: {age}\n"
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            elif a.quote.quote_type.value == "property":
                name = payload.get("full_name")
                contact = payload.get("contact")
                subject = payload.get("subject", "?")
                loc = payload.get("address_or_city", "?")
                value = payload.get("property_value", "?")
                comment = payload.get("comment")
                details = (
                    "\n🧮 Имущество расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Что страхуем: {subject}\n"
                    + f"- Локация: {loc}\n"
                    + f"- Стоимость: {value} BYN\n"
                    + (f"- Комментарий: {comment}\n" if comment else "")
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            elif a.quote.quote_type.value in {"cargo", "accident", "cmr", "dms", "other"}:
                name = payload.get("full_name")
                contact = payload.get("contact")
                subject = payload.get("subject", "?")
                value = payload.get("insured_value", "?")
                comment = payload.get("comment")
                extra = payload.get("extra_type")
                kind_map = {
                    "cargo": "📦 Грузы",
                    "accident": "🩹 Несчастные случаи",
                    "cmr": "🚚 CMR",
                    "dms": "🩺 ДМС",
                    "other": "✍️ Другой вид",
                }
                kind_title = kind_map.get(a.quote.quote_type.value, "✍️ Другой вид")
                if a.quote.quote_type.value == "other" and extra:
                    kind_title = f"{kind_title} ({extra})"
                details = (
                    f"\n🧮 {kind_title} расчёт:\n"
                    + (f"- Имя: {name}\n" if name else "")
                    + (f"- Контакт: {contact}\n" if contact else "")
                    + f"- Что страхуем: {subject}\n"
                    + f"- Стоимость: {value} BYN\n"
                    + (f"- Комментарий: {comment}\n" if comment else "")
                    + f"- Итог: {premium:.2f} {a.quote.currency}\n"
                    + f"- Quote ID: {a.quote.id}"
                )
            else:
                details = f"\n🧮 Расчёт: {premium:.2f} {a.quote.currency}\n- Quote ID: {a.quote.id}"

        desc = f"\n📝 Комментарий: {a.description}" if a.description else ""
        await message.answer(header + details + desc, reply_markup=application_actions_keyboard(a.id))

    await message.answer("Меню.", reply_markup=agent_menu())


@router.callback_query(F.data.startswith("app_status:"))
async def app_status_change(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if not (await _ensure_agent_tg(callback.from_user.id)):
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        _, app_id_s, status_s = callback.data.split(":", 2)
        app_id = int(app_id_s)
    except Exception:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    if status_s == "in_progress":
        status = ApplicationStatus.in_progress
    elif status_s == "done":
        status = ApplicationStatus.done
    else:
        await callback.answer("Некорректный статус", show_alert=True)
        return

    app = await set_application_status(app_id, status=status)
    if app is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    await callback.answer(f"Статус: {app.status.value}")


@router.message(F.text == Btn.MY_CLIENTS)
async def agent_clients(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    await message.answer("Раздел «Мои клиенты» (заглушка).", reply_markup=agent_menu())

@router.message(F.text == Btn.REPORTS)
async def agent_reports(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    await message.answer("Раздел «Отчёты» (заглушка).", reply_markup=agent_menu())


@router.message(F.text == Btn.SETTINGS)
async def agent_settings(message: Message) -> None:
    if not await _ensure_agent(message):
        return
    await message.answer("Раздел «Настройки» (заглушка).", reply_markup=agent_menu())
