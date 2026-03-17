# Telegram бот (aiogram 3 + PostgreSQL)

Бот определяет роль пользователя (агент/клиент) через inline-кнопки и показывает разные меню. Данные хранятся в PostgreSQL через async SQLAlchemy.

## Установка

1) Создайте виртуальное окружение и установите зависимости:

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

2) Создайте файл `.env` рядом с `README.md` (можно скопировать из `.env.example`) и заполните:

- `BOT_TOKEN`
- `DATABASE_URL` (формат `postgresql+asyncpg://user:pass@host:5432/dbname`)

3) Подготовьте БД (бот сам создаст таблицы при старте).

## Запуск

```bash
python -m bot
```

## Структура

- `bot/__main__.py` — точка входа
- `bot/config.py` — настройки из `.env`
- `bot/db/` — подключение к БД и модели
- `bot/handlers/` — хэндлеры и меню
- `bot/keyboards.py` — клавиатуры (inline/reply)
