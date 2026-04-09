import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import get_settings
from bot.db.base import init_db, migrate_agent_tenants, migrate_tariff_tables
from bot.db.repo import set_superadmin
from bot.handlers.router import router
from bot.scheduler.reminders import reminders_worker
from bot.scheduler.payment_reminders import payment_reminders_worker


async def on_startup() -> None:
    await init_db()
    await migrate_agent_tenants()
    await migrate_tariff_tables()
    settings = get_settings()
    if settings.superadmin_tg_id:
        await set_superadmin(settings.superadmin_tg_id)


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    await on_startup()

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    asyncio.create_task(reminders_worker(bot))
    asyncio.create_task(payment_reminders_worker(bot))

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
