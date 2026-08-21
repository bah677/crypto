import logging
from typing import Any, Awaitable, Callable

from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.dispatcher.middlewares.user_context import UserContextMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Chat, Message, TelegramObject, Update

from app.bot.admin_guard import is_admin_user

log = logging.getLogger(__name__)


class PrivateChatOnlyMiddleware(BaseMiddleware):
    """Не обрабатывает сообщения из групп/каналов — только личка с ботом.

    Исключение: callback с pump-алерта в группе/топике
    (ордер, EMA-будильник, слежение до входа).
    """

    _GROUP_CALLBACK_PREFIXES = (
        "pump:pos:",
        "pump:alarm:",
        "pump:watch:",
    )

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat = _chat_from_event(event)
        if isinstance(event, CallbackQuery) and event.data:
            data_s = event.data
            if any(data_s.startswith(p) for p in self._GROUP_CALLBACK_PREFIXES):
                return await handler(event, data)
        if chat is None or chat.type != ChatType.PRIVATE:
            if chat is not None:
                log.debug(
                    "Игнор апдейта: не личка (chat_id=%s type=%s)",
                    chat.id,
                    chat.type,
                )
            return None
        return await handler(event, data)


class SuperAdminMiddleware(BaseMiddleware):
    """Пропускает только Telegram user id из таблицы admins."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        uid = self._user_id(event)
        if uid is None:
            log.warning(
                "Отказ в доступе: не удалось определить user_id для %s",
                type(event).__name__,
            )
            await self._deny(event)
            return None
        if not await is_admin_user(uid):
            log.warning("Отказ в доступе: user_id=%s (нет в admins)", uid)
            await self._deny(event)
            return None
        return await handler(event, data)

    @staticmethod
    def _user_id(event: TelegramObject) -> int | None:
        if isinstance(event, Update):
            return UserContextMiddleware.resolve_event_context(event).user_id
        if isinstance(event, Message) and event.from_user:
            return event.from_user.id
        if isinstance(event, CallbackQuery) and event.from_user:
            return event.from_user.id
        user = getattr(event, "from_user", None)
        return user.id if user is not None else None

    @staticmethod
    async def _deny(event: TelegramObject) -> None:
        text = "Доступ запрещён. Бот доступен только администраторам из таблицы admins."
        if isinstance(event, Update):
            if event.message:
                await event.message.answer(text)
            elif event.callback_query:
                await event.callback_query.answer(text, show_alert=True)
            return
        if isinstance(event, Message):
            await event.answer(text)
        elif isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)


def _chat_from_event(event: TelegramObject) -> Chat | None:
    if isinstance(event, Update):
        if event.message:
            return event.message.chat
        if event.edited_message:
            return event.edited_message.chat
        if event.callback_query and event.callback_query.message:
            return event.callback_query.message.chat
        return None
    if isinstance(event, Message):
        return event.chat
    if isinstance(event, CallbackQuery) and event.message:
        return event.message.chat
    return None
