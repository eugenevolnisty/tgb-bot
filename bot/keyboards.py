from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def role_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Я агент", callback_data="role:agent"),
                InlineKeyboardButton(text="Я клиент", callback_data="role:client"),
            ]
        ]
    )


def client_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Консультация"), KeyboardButton(text="Рассчитать стоимость")],
            [KeyboardButton(text="Оставить заявку")],
            [KeyboardButton(text="Мои договоры"), KeyboardButton(text="Мои документы")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def agent_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Входящие заявки"), KeyboardButton(text="Мои клиенты")],
            [KeyboardButton(text="Напоминания"), KeyboardButton(text="Отчёты")],
            [KeyboardButton(text="Настройки")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )
