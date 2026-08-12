from __future__ import annotations

import logging

from aiogram import Bot

from app.db.session import session_scope
from app.repository.admins import list_alert_admins

log = logging.getLogger(__name__)

_bot: Bot | None = None


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


async def notify_admins(text: str) -> int:
    if _bot is None:
        log.error("Bot not set — cannot notify")
        return 0
    async with session_scope() as session:
        admins = await list_alert_admins(session)
    sent = 0
    for row in admins:
        try:
            await _bot.send_message(row.telegram_user_id, text, parse_mode="HTML")
            sent += 1
        except Exception:
            log.exception("notify failed uid=%s", row.telegram_user_id)
    return sent
