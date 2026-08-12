"""Gold Adviser — XAU/USD M1 anomaly scanner via Telegram."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.bot.handlers import panel
from app.bot.middlewares import AdminOnlyMiddleware
from app.config import get_settings
from app.db.session import init_db
from app.logging_setup import setup_file_logging
from app.services.notify import set_bot
from app.services.scan import run_gold_scan_tick


async def main() -> None:
    settings = get_settings()
    setup_file_logging(settings.log_level)
    log = logging.getLogger(__name__)
    log.info("gold_adviser starting…")

    await init_db()

    bot = Bot(
        settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    set_bot(bot)
    await bot.set_my_commands(
        [
            BotCommand(command="gold", description="Панель Gold Adviser"),
            BotCommand(command="on", description="Включить сканер"),
            BotCommand(command="off", description="Выключить сканер"),
            BotCommand(command="status", description="Статус"),
            BotCommand(command="admins", description="Список админов"),
            BotCommand(command="help", description="Справка"),
        ]
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(AdminOnlyMiddleware())
    dp.include_router(panel.router)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_gold_scan_tick,
        CronTrigger(second=settings.scan_second, timezone="UTC"),
        id="gold_m1_scan",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info(
        "scheduler: gold_m1_scan every minute at :%02d UTC",
        settings.scan_second,
    )

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
