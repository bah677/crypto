from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from app.bot.admin_guard import is_admin_user

log = logging.getLogger(__name__)


class LogUpdatesMiddleware(BaseMiddleware):
    """Логирует каждый входящий Update (для отладки доставки)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            kind = "unknown"
            preview = ""
            uid = None
            if event.message:
                kind = "message"
                preview = (event.message.text or event.message.caption or "")[:80]
                uid = event.message.from_user.id if event.message.from_user else None
            elif event.callback_query:
                kind = "callback"
                preview = (event.callback_query.data or "")[:80]
                uid = event.callback_query.from_user.id if event.callback_query.from_user else None
            log.info("update id=%s kind=%s uid=%s text=%r", event.update_id, kind, uid, preview)
        return await handler(event, data)


class AdminOnlyMiddleware(BaseMiddleware):
    """Только админы. Вешать на message / callback_query (event = Message|CallbackQuery)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        uid = self._user_id(event)
        if uid is None:
            log.warning("admin deny: no user_id on %s", type(event).__name__)
            await self._deny(event)
            return None
        if not await is_admin_user(uid):
            log.warning("admin deny: uid=%s not in admins", uid)
            await self._deny(event)
            return None
        return await handler(event, data)

    @staticmethod
    def _user_id(event: TelegramObject) -> int | None:
        if isinstance(event, Message) and event.from_user:
            return int(event.from_user.id)
        if isinstance(event, CallbackQuery) and event.from_user:
            return int(event.from_user.id)
        return None

    @staticmethod
    async def _deny(event: TelegramObject) -> None:
        try:
            if isinstance(event, CallbackQuery):
                await event.answer("Нет доступа", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("Доступ только для админов gold_adviser.")
        except Exception:
            log.exception("deny reply failed")
