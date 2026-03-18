from aiogram import Router

from bot.handlers.start import router as start_router
from bot.handlers.client import router as client_router
from bot.handlers.agent import router as agent_router
from bot.handlers.kasko import router as kasko_router
from bot.handlers.application_flow import router as application_router
from bot.handlers.property_calc import router as property_router
from bot.handlers.generic_calcs import router as generic_router
from bot.handlers.reminders import router as reminders_router

router = Router()
router.include_router(start_router)
router.include_router(client_router)
router.include_router(agent_router)
router.include_router(kasko_router)
router.include_router(application_router)
router.include_router(property_router)
router.include_router(generic_router)
router.include_router(reminders_router)

