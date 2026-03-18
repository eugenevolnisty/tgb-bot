from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


class Btn:
    # Roles
    ROLE_AGENT = "🧑‍💼 Я агент"
    ROLE_CLIENT = "🙋 Я клиент"

    # Client menu
    CALC_PRICE = "🧮 Рассчитать стоимость"
    LEAVE_APP = "📝 Оставить заявку"
    MY_CONTRACTS = "📄 Мои договоры"
    MY_DOCS = "📎 Мои документы"
    SWITCH_TO_AGENT = "🔁 Переключиться на агента"

    # Agent menu
    INCOMING = "📥 Входящие заявки"
    IN_PROGRESS = "🛠 Заявки в работе"
    MY_CLIENTS = "👥 Мои клиенты"
    REMINDERS = "⏰ Напоминания"
    REPORTS = "📊 Отчёты"
    SETTINGS = "⚙️ Настройки"
    SWITCH_TO_CLIENT = "🔁 Переключиться на клиента"


def role_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=Btn.ROLE_AGENT, callback_data="role:agent"),
                InlineKeyboardButton(text=Btn.ROLE_CLIENT, callback_data="role:client"),
            ]
        ]
    )


def client_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=Btn.CALC_PRICE)],
            [KeyboardButton(text=Btn.LEAVE_APP)],
            [KeyboardButton(text=Btn.MY_CONTRACTS), KeyboardButton(text=Btn.MY_DOCS)],
            [KeyboardButton(text=Btn.SWITCH_TO_AGENT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def agent_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=Btn.INCOMING), KeyboardButton(text=Btn.IN_PROGRESS)],
            [KeyboardButton(text=Btn.MY_CLIENTS)],
            [KeyboardButton(text=Btn.REMINDERS), KeyboardButton(text=Btn.REPORTS)],
            [KeyboardButton(text=Btn.SETTINGS)],
            [KeyboardButton(text=Btn.SWITCH_TO_CLIENT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def apply_quote_keyboard(quote_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оформить заявку по этому расчёту", callback_data=f"quote_apply:{quote_id}")]
        ]
    )


def application_actions_keyboard(app_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Взять в работу", callback_data=f"app_status:{app_id}:in_progress"),
                InlineKeyboardButton(text="🏁 Закрыть", callback_data=f"app_status:{app_id}:done"),
            ]
        ]
    )


def insurance_type_keyboard(prefix: str) -> InlineKeyboardMarkup:
    """
    prefix example:
      - "app"  -> callback_data "app:type:kasko"
      - "calc" -> callback_data "calc:type:kasko"
    """
    rows = [
        [
            InlineKeyboardButton(text="🚗 КАСКО", callback_data=f"{prefix}:type:kasko"),
            InlineKeyboardButton(text="🏠 Имущество", callback_data=f"{prefix}:type:property"),
        ],
        [
            InlineKeyboardButton(text="📦 Грузы", callback_data=f"{prefix}:type:cargo"),
            InlineKeyboardButton(text="🩹 Несчастные случаи", callback_data=f"{prefix}:type:accident"),
        ],
        [
            InlineKeyboardButton(text="🚚 CMR", callback_data=f"{prefix}:type:cmr"),
            InlineKeyboardButton(text="🩺 ДМС", callback_data=f"{prefix}:type:dms"),
        ],
        [InlineKeyboardButton(text="✍️ Другой вид", callback_data=f"{prefix}:type:other")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
