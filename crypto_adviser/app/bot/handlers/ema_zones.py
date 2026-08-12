"""Команда /zones — зоны EMA по всем заданиям советчика."""

from __future__ import annotations

import asyncio

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.advisor.tasks import advisor_task_from_row
from app.config import get_settings
from app.db.session import session_scope
from app.repository.advisor_tasks import fetch_all_advisor_tasks
from app.services.ema_zones_report import (
    build_zones_report_sync,
    split_report_messages,
)

router = Router()


@router.message(Command("zones", "ema_zones", "zones_report"))
async def cmd_zones(message: Message) -> None:
    if not get_settings().is_advisor_mode:
        await message.answer("Команда доступна только в режиме <b>советчик</b> (BOT_MODE=advisor).")
        return

    await message.answer("⏳ Считаю зоны EMA по заданиям…")

    async with session_scope() as session:
        rows = await fetch_all_advisor_tasks(session)
    tasks = [advisor_task_from_row(r) for r in rows]

    try:
        report, _errors = await asyncio.to_thread(build_zones_report_sync, tasks)
    except Exception as e:
        await message.answer(f"⚠️ Ошибка отчёта: {e}")
        return

    for i, part in enumerate(split_report_messages(report)):
        if i == 0:
            await message.answer(part)
        else:
            await message.answer(part)
