"""Команда /sl — уровни SL (цена пересечения EMA) по позициям и watch."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import get_settings
from app.services.ema_sl_monitor import run_ema_sl_command

router = Router()
log = logging.getLogger(__name__)

_MSG_MAX = 4000


def _split_answer(text: str) -> list[str]:
    if len(text) <= _MSG_MAX:
        return [text]
    parts: list[str] = []
    buf = ""
    for line in text.split("\n"):
        piece = f"{buf}\n{line}" if buf else line
        if len(piece) <= _MSG_MAX:
            buf = piece
        else:
            if buf:
                parts.append(buf)
            buf = line
    if buf:
        parts.append(buf)
    return parts or [text[:_MSG_MAX]]


@router.message(Command("sl", "ema_sl"))
async def cmd_sl(message: Message) -> None:
    if not get_settings().is_advisor_mode:
        await message.answer("Команда доступна только в режиме <b>советчик</b>.")
        return

    await message.answer("⏳ Считаю уровни SL (EMA cross)…")
    try:
        text = await run_ema_sl_command()
    except Exception as e:
        log.exception("Команда /sl")
        await message.answer(f"⚠️ Ошибка расчёта SL: {e}")
        return

    for i, part in enumerate(_split_answer(text)):
        if i == 0:
            await message.answer(part)
        else:
            await message.answer(part)
