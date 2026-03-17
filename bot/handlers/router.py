from aiogram import Router

from bot.handlers.start import router as start_router
from bot.handlers.client import router as client_router
from bot.handlers.agent import router as agent_router

router = Router()
router.include_router(start_router)
router.include_router(client_router)
router.include_router(agent_router)

