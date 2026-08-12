"""Личные сообщения из pump-кнопок (алерт в группе → мастер в личке)."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

log = logging.getLogger(__name__)


async def _bot_username(bot: Bot) -> str:
    me = await bot.get_me()
    return me.username or "bot"


async def send_pump_private(
    cb: CallbackQuery,
    text: str,
    *,
    parse_mode: str = "HTML",
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Отправить в личку; False если пользователь не нажимал /start у бота."""
    if not cb.from_user:
        return False
    try:
        await cb.bot.send_message(
            cb.from_user.id,
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return True
    except TelegramForbiddenError:
        log.info(
            "Pump DM blocked: user_id=%s (нужен /start в личке)",
            cb.from_user.id,
        )
        return False
    except Exception:
        log.exception("Pump DM failed user_id=%s", cb.from_user.id)
        return False


async def answer_pump_callback_continue(
    cb: CallbackQuery,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    ok_answer: str = "Продолжаем",
) -> bool:
    """
    Продолжить мастер ордера.
    В личке — сразу в этом чате; из группы — в личку с ботом.
    """
    if not cb.from_user:
        await cb.answer()
        return False

    if (
        cb.message
        and cb.message.chat
        and cb.message.chat.type == ChatType.PRIVATE
    ):
        await cb.answer(ok_answer)
        log.info("Pump wizard in private chat user=%s", cb.from_user.id)
        await cb.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        return True

    return await answer_pump_callback_and_dm(
        cb,
        dm_text=text,
        dm_reply_markup=reply_markup,
        ok_answer="Продолжим в личке с ботом",
    )


async def answer_pump_callback_and_dm(
    cb: CallbackQuery,
    *,
    dm_text: str,
    dm_reply_markup: InlineKeyboardMarkup | None = None,
    ok_answer: str = "Продолжим в личке с ботом",
) -> bool:
    """
    Ответ на клик в группе + мастер в личке.
    Если личка недоступна — alert и подсказка в топике.
    """
    if not cb.from_user:
        await cb.answer()
        return False

    sent = await send_pump_private(
        cb,
        dm_text,
        reply_markup=dm_reply_markup,
    )
    if sent:
        await cb.answer(ok_answer)
        log.info("Pump wizard DM ok user=%s", cb.from_user.id)
        return True

    username = await _bot_username(cb.bot)
    await cb.answer("Нужен /start в личке с ботом", show_alert=True)
    if cb.message:
        try:
            await cb.message.reply(
                f"⚠️ <b>Чтобы открыть позицию</b>, напишите боту в личку "
                f"<a href=\"https://t.me/{username}?start=pump\">@{username}</a> "
                "и нажмите <b>/start</b>, затем снова кнопку в алерте.",
                parse_mode="HTML",
            )
        except Exception:
            log.exception("Pump: не отправили подсказку в топик")
    return False
