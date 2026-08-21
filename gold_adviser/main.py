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

from app.bot.handlers import panel
from app.bot.middlewares import AdminOnlyMiddleware, LogUpdatesMiddleware
from app.config import get_settings
from app.db.session import init_db
from app.logging_setup import setup_file_logging
from app.services.notify import set_bot
from app.services.scan import run_gold_fast_scan_loop


async def main() -> None:
    settings = get_settings()
    setup_file_logging(settings.log_level)
    log = logging.getLogger(__name__)
    log.info("gold_adviser starting…")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    await init_db()

    bot = Bot(
        settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    set_bot(bot)
    await bot.delete_webhook(drop_pending_updates=False)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Старт / панель"),
            BotCommand(command="gold", description="Панель Gold Adviser"),
            BotCommand(command="on", description="Включить сканер"),
            BotCommand(command="off", description="Выключить сканер"),
            BotCommand(command="status", description="Статус"),
            BotCommand(command="chart", description="График M1 по окну настроек"),
            BotCommand(command="admins", description="Список админов"),
            BotCommand(command="help", description="Справка"),
        ]
    )

    dp = Dispatcher(storage=MemoryStorage())
    # outer: видим все апдейты до роутинга
    dp.update.outer_middleware(LogUpdatesMiddleware())
    # admin-check на уровне сообщений/кнопок (event уже Message/CallbackQuery)
    dp.message.middleware(AdminOnlyMiddleware())
    dp.callback_query.middleware(AdminOnlyMiddleware())
    dp.include_router(panel.router)

    scan_task = asyncio.create_task(
        run_gold_fast_scan_loop(),
        name="gold_fast_scan",
    )
    log.info(
        "scan loop: after_close=%ss poll=%ss (quota-friendly)",
        settings.scan_after_close_sec,
        settings.scan_poll_sec,
    )


    try:
        await bot.send_message(
            settings.superadmin_telegram_id,
            "✅ <b>gold_adviser</b> онлайн.\nНапишите /start или /gold",
        )
        log.info("startup ping sent to superadmin=%s", settings.superadmin_telegram_id)
    except Exception:
        log.exception("startup ping failed")

    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        scan_task.cancel()
        try:
            await scan_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
