from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.db.models import UserRole
from bot.db.repo import get_or_create_user, has_agent_password
from bot.keyboards import Btn
from bot.services.agent_auth import is_agent_session_active


class AccessGuardMiddleware(BaseMiddleware):
    """
    Soft centralized guard:
    - blocks protected agent actions when agent password is configured
      and session is not active.
    - keeps dev flow intact for non-protected routes.
    """

    _agent_message_buttons = {
        Btn.INCOMING,
        Btn.IN_PROGRESS,
        Btn.MY_CLIENTS,
        Btn.DASHBOARD,
        Btn.ADD_PAYMENT,
        Btn.REMINDERS,
        Btn.REPORTS,
        Btn.SETTINGS,
    }

    _agent_callback_prefixes = (
        "app:",
        "app_status:",
        "agent:reports",
        "payrep:",
        "endrep:",
        "comrep:",
        "aset:",
        "acom:",
        "rem:",
        "clients:",
        "client:",
        "contract:",
        "payins:",
        "agentq:",
    )

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id

        is_superadmin = False
        if user_id is not None:
            user = await get_or_create_user(user_id)
            is_superadmin = user.role == UserRole.superadmin
        data["is_superadmin"] = is_superadmin
        if is_superadmin:
            return await handler(event, data)

        if isinstance(event, Message):
            text = (event.text or "").strip()
            if text in self._agent_message_buttons:
                user = await get_or_create_user(event.from_user.id)
                if user.role == UserRole.agent and await has_agent_password(event.from_user.id):
                    if not is_agent_session_active(event.from_user.id):
                        await event.answer("🔐 Сессия агента не активна. Нажмите /start и введите пароль.")
                        return None

        if isinstance(event, CallbackQuery):
            cd = (event.data or "").strip()
            if cd.startswith(self._agent_callback_prefixes):
                user = await get_or_create_user(event.from_user.id)
                if user.role == UserRole.agent and await has_agent_password(event.from_user.id):
                    if not is_agent_session_active(event.from_user.id):
                        await event.answer("Сессия не активна. Нажмите /start.", show_alert=True)
                        return None

        return await handler(event, data)
