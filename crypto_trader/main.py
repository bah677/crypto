import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.bot.handlers import (
    advisor_tasks,
    alerts_menu,
    atr_pullback,
    common,
    ema_sl,
    ema_zones,
    price_watch,
    pump_open_position,
    pump_scan,
    scalp_advisor,
    sl_follow,
    tasks,
)
from app.bot.middlewares import PrivateChatOnlyMiddleware, SuperAdminMiddleware
from app.config import get_settings
from app.db.session import init_db
from app.logging_setup import BOT_LOG, ERR_LOG, setup_file_logging
from app.mt5.session import mt5_shutdown_all_async, mt5_startup_if_configured
from app.services.strategy import run_strategy_tick


async def main() -> None:
    settings = get_settings()
    setup_file_logging(settings.log_level)
    log = logging.getLogger(__name__)
    log.info("Лог-файлы: bot=%s err=%s", BOT_LOG, ERR_LOG)
    await init_db()

    if settings.is_trading_mode:
        mt5_startup_if_configured()
    else:
        from app.db.session import session_scope
        from app.repository.advisor_tasks import count_advisor_tasks

        async with session_scope() as session:
            n = await count_advisor_tasks(session)
        log.info(
            "BOT_MODE=advisor: %s заданий в БД → Telegram, ордера отключены",
            n,
        )

    bot = Bot(
        settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    if settings.is_advisor_mode:
        await bot.set_my_commands(
            [
                BotCommand(command="task_add", description="Новое задание"),
                BotCommand(command="tasks", description="Список заданий"),
                BotCommand(command="status", description="Статус и задания"),
                BotCommand(command="help", description="Справка"),
                BotCommand(command="cancel", description="Отмена мастера"),
                BotCommand(command="funding_scan", description="Скан funding сейчас"),
                BotCommand(command="watch_list", description="Мониторинг цены"),
                BotCommand(command="watch_add", description="Добавить в мониторинг"),
                BotCommand(command="zones", description="Зоны EMA"),
                BotCommand(command="sl", description="Уровни SL EMA"),
                BotCommand(command="sl_follow", description="Авто-SL на позиции"),
                BotCommand(command="sl_follow_list", description="Список авто-SL"),
                BotCommand(command="atr_add", description="ATR Pullback задание"),
                BotCommand(command="atr_tasks", description="ATR Pullback список"),
                BotCommand(command="scalp_add", description="Scalp M5/M1 задание"),
                BotCommand(command="scalp_tasks", description="Scalp список"),
                BotCommand(command="pump", description="Pump&Dump сканер"),
                BotCommand(command="pump_fade", description="Fade A/B: filter + OI block"),
                BotCommand(command="pump_alarms", description="EMA-будильники"),
                BotCommand(command="pump_watches", description="Слежение до входа"),
                BotCommand(command="test_order", description="Тест pump-алерта с кнопками"),
                BotCommand(command="admins", description="Список админов бота"),
                BotCommand(command="pump_tvh", description="ТВХ: вотчлист и настройки"),
                BotCommand(command="sl_anom_follow", description="Закрытие по аномальному минутному телу"),
                BotCommand(command="sl_anom_list", description="Список авто-закрытий"),
                BotCommand(command="sl_anom_master", description="Мастер настроек авто-закрытий"),
            ]
        )
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(PrivateChatOnlyMiddleware())
    dp.update.middleware(SuperAdminMiddleware())
    dp.include_router(common.router)
    dp.include_router(alerts_menu.router)
    dp.include_router(advisor_tasks.router)
    dp.include_router(tasks.router)
    dp.include_router(price_watch.router)
    dp.include_router(ema_zones.router)
    dp.include_router(ema_sl.router)
    dp.include_router(sl_follow.router)
    from app.bot.handlers import sl_anom_close
    dp.include_router(sl_anom_close.router)
    dp.include_router(atr_pullback.router)
    dp.include_router(scalp_advisor.router)
    dp.include_router(pump_scan.router)
    dp.include_router(pump_open_position.router)
    from app.bot.handlers import pump_ema_alarm

    dp.include_router(pump_ema_alarm.router)
    from app.bot.handlers import pump_entry_watch

    dp.include_router(pump_entry_watch.router)

    scheduler = AsyncIOScheduler()
    if not settings.pump_only_mode:
        scheduler.add_job(
            run_strategy_tick,
            IntervalTrigger(seconds=2),
            id="strategy_tick",
            max_instances=1,
            coalesce=True,
        )
    if not settings.pump_only_mode and settings.funding_scan_enabled:
        from app.services.funding_scan import run_funding_scan

        scheduler.add_job(
            run_funding_scan,
            CronTrigger(minute=55, timezone="Europe/Moscow"),
            id="funding_scan",
            max_instances=1,
            coalesce=True,
        )
    if (
        not settings.pump_only_mode
        and settings.is_advisor_mode
        and settings.price_spike_monitor_enabled
    ):
        from app.services.price_spike_monitor import run_price_spike_tick

        scheduler.add_job(
            run_price_spike_tick,
            CronTrigger(second=0, timezone="Europe/Moscow"),
            id="price_spike_monitor",
            max_instances=1,
            coalesce=True,
        )
    if (
        not settings.pump_only_mode
        and settings.is_advisor_mode
        and settings.ema_sl_monitor_enabled
    ):
        from app.services.ema_sl_monitor import run_ema_sl_tick

        scheduler.add_job(
            run_ema_sl_tick,
            CronTrigger(second="5,20,35,50", timezone="Europe/Moscow"),
            id="ema_sl_monitor",
            max_instances=1,
            coalesce=True,
        )
    if not settings.pump_only_mode and settings.sl_follow_monitor_enabled:
        from app.services.auto_sl_follow_monitor import run_sl_follow_tick

        scheduler.add_job(
            run_sl_follow_tick,
            CronTrigger(second="12,27,42,57", timezone="Europe/Moscow"),
            id="sl_follow_monitor",
            max_instances=1,
            coalesce=True,
        )
    if not settings.pump_only_mode and settings.sl_anom_close_monitor_enabled:
        from app.services.sl_anom_close_monitor import run_sl_anom_close_tick

        # 1m свеча закрывается в +60с, поэтому запускаем через 10 секунд от начала минуты
        scheduler.add_job(
            run_sl_anom_close_tick,
            CronTrigger(minute="*", second=10, timezone="Europe/Moscow"),
            id="sl_anom_close_monitor",
            max_instances=1,
            coalesce=True,
        )
    if settings.pump_scan_enabled:
        from app.services.pump_scan import run_pump_scan_tick, run_pump_slow_tf_scan, run_pump_universe_refresh

        scheduler.add_job(
            run_pump_universe_refresh,
            CronTrigger(minute=0, timezone="Europe/Moscow"),
            id="pump_universe_refresh",
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            run_pump_scan_tick,
            CronTrigger(
                minute="1,6,11,16,21,26,31,36,41,46,51,56",
                timezone="Europe/Moscow",
            ),
            id="pump_scan",
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            run_pump_slow_tf_scan,
            CronTrigger(minute=8, timezone="Europe/Moscow"),
            id="pump_slow_tf_scan",
            max_instances=1,
            coalesce=True,
        )
        # TVH монитор отключён — только pump-алерты
        from app.services.pump_outcome_eval import run_pump_outcome_eval_tick

        scheduler.add_job(
            run_pump_outcome_eval_tick,
            IntervalTrigger(minutes=10),
            id="pump_outcome_eval",
            max_instances=1,
            coalesce=True,
        )
    if settings.bot_order_watch_enabled:
        from app.services.bot_order_watch import run_bot_order_watch_tick

        scheduler.add_job(
            run_bot_order_watch_tick,
            IntervalTrigger(seconds=settings.bot_order_watch_interval_sec),
            id="bot_order_watch",
            max_instances=1,
            coalesce=True,
        )
    if settings.pump_ema_alarm_enabled:
        from app.services.pump_ema_alarm import run_pump_ema_alarm_tick

        scheduler.add_job(
            run_pump_ema_alarm_tick,
            IntervalTrigger(seconds=settings.pump_ema_alarm_interval_sec),
            id="pump_ema_alarm",
            max_instances=1,
            coalesce=True,
        )
    if settings.pump_entry_watch_enabled:
        from app.services.pump_entry_watch import run_pump_entry_watch_tick

        scheduler.add_job(
            run_pump_entry_watch_tick,
            IntervalTrigger(seconds=settings.pump_entry_watch_interval_sec),
            id="pump_entry_watch",
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()
    if settings.pump_only_mode:
        logging.getLogger(__name__).info(
            "PUMP_ONLY_MODE: фон — только pump-сканер (советчик и мониторы отключены)"
        )
    else:
        logging.getLogger(__name__).info(
            "Планировщик: советчик — опрос по ТФ + догон пропущенных свечей (см. advisor.py)"
        )
    if not settings.pump_only_mode and settings.funding_scan_enabled:
        logging.getLogger(__name__).info(
            "Funding scan: каждый час в :55 MSK, порог |годовые| > %s%%",
            settings.funding_annual_threshold,
        )
    if (
        not settings.pump_only_mode
        and settings.is_advisor_mode
        and settings.price_spike_monitor_enabled
    ):
        logging.getLogger(__name__).info(
            "Price spike: :20 MSK (после EMA), приоритет ниже, порог %.1f×",
            settings.price_spike_ratio,
        )
    if (
        not settings.pump_only_mode
        and settings.is_advisor_mode
        and settings.ema_sl_monitor_enabled
    ):
        logging.getLogger(__name__).info(
            "EMA SL: :05/:20/:35/:50 MSK (после закрытия 15m свечи)"
        )
    if not settings.pump_only_mode and settings.sl_follow_monitor_enabled:
        logging.getLogger(__name__).info(
            "SL follow: :12/:27/:42/:57 MSK, перенос SL на Bybit"
        )
    if settings.pump_scan_enabled:
        s = settings
        logging.getLogger(__name__).info(
            "Pump scan: пул :00 · fast TF :01/… · slow 4h/1D :08 → "
            "pump+EMA1D в топик %s (ТВХ выкл)",
            s.telegram_alerts_topic_pump,
        )
    if settings.bot_order_watch_enabled:
        logging.getLogger(__name__).info(
            "Order watch: каждые %s с — статус ордеров бота → reply в Telegram",
            settings.bot_order_watch_interval_sec,
        )
    if settings.pump_ema_alarm_enabled:
        logging.getLogger(__name__).info(
            "EMA alarm: каждые %s с — пересечение цены и EMA (приоритет ниже pump scan)",
            settings.pump_ema_alarm_interval_sec,
        )
    if settings.pump_entry_watch_enabled:
        logging.getLogger(__name__).info(
            "Entry watch: каждые %s с — слежение до окна входа (LLM cooldown %ss)",
            settings.pump_entry_watch_interval_sec,
            settings.pump_entry_watch_llm_cooldown_sec,
        )
    if settings.deepseek_ready:
        logging.getLogger(__name__).info(
            "DeepSeek: мнение LLM reply на каждый pump-алерт"
        )
    if (
        not settings.pump_only_mode
        and settings.is_advisor_mode
        and settings.atr_pullback_enabled
    ):
        logging.getLogger(__name__).info(
            "ATR Pullback trail: :12/:27/:42/:57 MSK, SL slow−1ATR"
        )

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        if settings.is_trading_mode:
            await mt5_shutdown_all_async()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
