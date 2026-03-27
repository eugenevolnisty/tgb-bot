from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


class Btn:
    # Roles
    ROLE_AGENT = "🧑‍💼 Я агент"
    ROLE_CLIENT = "🙋 Я клиент"

    # Client menu
    CALC_PRICE = "🧮 Рассчитать стоимость и оставить заявку"
    LEAVE_APP = "📝 Оставить заявку"
    MY_CONTRACTS = "📄 Мои договоры"
    MY_DOCS = "📎 Мои документы"
    CONTACT_AGENT = "📞 Связаться с агентом"
    NEXT_PAYMENT = "⏳ Когда у меня ближайший взнос?"
    SWITCH_TO_AGENT = "🔁 Переключиться на агента"

    # Agent menu
    INCOMING = "📥 Входящие заявки"
    IN_PROGRESS = "🛠 Заявки в работе"
    MY_CLIENTS = "👥 Мои клиенты"
    DASHBOARD = "📈 Дашборд"
    REMINDERS = "⏰ Напоминания"
    REPORTS = "📊 Отчёты"
    SETTINGS = "⚙️ Настройки"
    ADD_PAYMENT = "💰 Внести взнос"
    SWITCH_TO_CLIENT = "🔁 Переключиться на клиента"
    MAIN_MENU = "🏠 Вернуться в главное меню"


def role_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=Btn.ROLE_AGENT, callback_data="role:agent"),
                InlineKeyboardButton(text=Btn.ROLE_CLIENT, callback_data="role:client"),
            ]
        ]
    )


def client_menu(*, allow_switch_to_agent: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=Btn.CALC_PRICE)],
        [KeyboardButton(text=Btn.MY_CONTRACTS), KeyboardButton(text=Btn.MY_DOCS)],
        [KeyboardButton(text=Btn.CONTACT_AGENT)],
        [KeyboardButton(text=Btn.NEXT_PAYMENT)],
    ]
    if allow_switch_to_agent:
        rows.append([KeyboardButton(text=Btn.SWITCH_TO_AGENT)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def agent_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=Btn.INCOMING), KeyboardButton(text=Btn.IN_PROGRESS)],
            [KeyboardButton(text=Btn.DASHBOARD)],
            [KeyboardButton(text=Btn.MY_CLIENTS)],
            [KeyboardButton(text=Btn.ADD_PAYMENT)],
            [KeyboardButton(text=Btn.REMINDERS), KeyboardButton(text=Btn.REPORTS)],
            [KeyboardButton(text=Btn.SETTINGS)],
            [KeyboardButton(text=Btn.SWITCH_TO_CLIENT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def to_main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=Btn.MAIN_MENU)]],
        resize_keyboard=True,
        input_field_placeholder="Сценарий",
    )


def apply_quote_keyboard(quote_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оформить заявку по этому расчёту", callback_data=f"quote_apply:{quote_id}")]
        ]
    )


def application_actions_keyboard(app_id: int, *, in_progress: bool = False, has_notes: bool = False) -> InlineKeyboardMarkup:
    if in_progress:
        rows = [[InlineKeyboardButton(text="🗑 Удалить", callback_data=f"app:delete:{app_id}")]]
        rows.append([InlineKeyboardButton(text="➕ Добавить заметку", callback_data=f"app:note:add:{app_id}")])
        note_text = "📒 Заметки" if not has_notes else "📒 Заметки (есть)"
        rows.append([InlineKeyboardButton(text=note_text, callback_data=f"app:note:list:{app_id}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Взять в работу", callback_data=f"app:take:{app_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"app:delete:{app_id}"),
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
            InlineKeyboardButton(text="✈️ Страховка за границу", callback_data=f"{prefix}:type:accident"),
        ],
        [
            InlineKeyboardButton(text="🚚 CMR", callback_data=f"{prefix}:type:cmr"),
            InlineKeyboardButton(text="🩺 ДМС", callback_data=f"{prefix}:type:dms"),
        ],
        [
            InlineKeyboardButton(text="🚛 Ответственность экспедитора", callback_data=f"{prefix}:type:expeditor"),
            InlineKeyboardButton(text="✍️ Другой вид", callback_data=f"{prefix}:type:other"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
