from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.bot.admin_guard import is_admin_user


class AdminOnlyMiddleware(BaseMiddleware):
    """Только админы могут взаимодействовать с ботом."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, Message) and event.from_user:
            user = event.from_user
        elif isinstance(event, CallbackQuery) and event.from_user:
            user = event.from_user
        else:
            user = data.get("event_from_user")

        uid = getattr(user, "id", None)
        if not uid or not await is_admin_user(int(uid)):
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer("Нет доступа", show_alert=True)
                except Exception:
                    pass
            elif isinstance(event, Message):
                try:
                    await event.answer("Доступ только для админов gold_adviser.")
                except Exception:
                    pass
            return None
        return await handler(event, data)
