from __future__ import annotations

from app.advisor.tasks import AdvisorTask
from app.config import Settings

# Лимит Telegram ~4096; запас под HTML
_HELP_CHUNK_MAX = 3600


def _funding_help_block(settings: Settings) -> str:
    enabled = "включён" if settings.funding_scan_enabled else "выключен"
    return (
        "<b>Funding scan</b>\n"
        f"Авто: <b>{enabled}</b> · :55 MSK · порог |годовые| &gt; "
        f"<b>{settings.funding_annual_threshold:.0f}%</b> · топ-{settings.funding_top_n} альтов\n"
        "Топик <b>фандинг</b> в группе. Команды: <code>/funding_scan</code>, <code>/funding</code> "
        "(<code>-</code> = из .env).\n"
        "<code>FUNDING_SCAN_ENABLED</code> · <code>FUNDING_ANNUAL_THRESHOLD</code> · "
        "<code>FUNDING_TOP_N</code>\n\n"
    )


def _ema_sl_help_block(settings: Settings) -> str:
    enabled = "включён" if settings.ema_sl_monitor_enabled else "выключен"
    return (
        "<b>SL EMA (уровень пересечения)</b>\n"
        f"Авто: <b>{enabled}</b> · :05/:20/:35/:50 MSK · отчёт при новой свече <b>базового ТФ</b>\n"
        "Позиции linear + <code>/watch_add</code>, только <b>включённые</b> задания.\n"
        "Два SL: базовый ТФ задания и младший (МТФ), цена закрытия следующей свечи при EMA cross. "
        "Топик "
        "<code>TELEGRAM_ALERTS_TOPIC_EMA_SL</code>\n"
        "<code>/sl</code> — тот же отчёт в личку. Вкл/выкл авто в топик: <code>/alerts</code>.\n"
        "<code>EMA_SL_MONITOR_ENABLED</code>\n\n"
    )


def _pump_scan_help_block(settings: Settings) -> str:
    enabled = "включён" if settings.pump_scan_enabled else "выключен"
    pump_topic = settings.telegram_alerts_topic_pump
    lc = "вкл" if settings.lunarcrush_ready else "выкл (нужен LUNARCRUSH_API_KEY)"
    return (
        "<b>Pump scanner</b>\n"
        f"Модуль: <b>{enabled}</b> · только 🔥 pump (dump и ТВХ выкл)\n"
        "<b>Импульс</b> → алерт с EMA 1D и 1W, силой 🔥–🔥🔥🔥, галерея <b>1W + 1D + 5m</b> "
        "(на 5m — EMA 50/100/200 с дневки), DeepSeek-мнение и кнопки ордера/будильника.\n"
        "Сейчас алерты в <b>личку</b> суперадмина (<code>PUMP_ALERTS_TO_PRIVATE=1</code>).\n"
        "Кнопки работают в личке; из группы нужен <code>/start</code> у бота.\n"
        f"LunarCrush: <b>{lc}</b>\n"
        f"Топик pump → <code>TELEGRAM_ALERTS_TOPIC_PUMP</code>"
        f"{f'={pump_topic}' if pump_topic else ' (default 870)'}\n"
        "<code>/pump</code> — сканер, пул, настройки\n"
        "<code>/pump_fade</code> — A/B: downtrend_mode (filter/boost) и OI hard block\n"
        "<code>/pump_alarms</code> — список EMA-будильников (пересечение цены и EMA)\n"
        "<code>/pump_watches</code> — слежение до окна входа (Funding+OI + LLM)\n"
        "<code>/тест_ордер</code> — тестовый алерт с графиком и кнопками ордеров\n"
        "<code>PUMP_SCAN_ENABLED</code> · <code>LUNARCRUSH_API_KEY</code>\n\n"
    )


def _scalp_advisor_help_block(settings: Settings) -> str:
    enabled = "включён" if settings.scalp_advisor_enabled else "выключен"
    return (
        "<b>Scalp M5/M1</b>\n"
        f"Мониторинг: <b>{enabled}</b> · M5 кросс EMA20/50 + откат · M1 ADX + паттерн\n"
        "BB M1 по умолчанию выкл (вкл в ⚙️ Стратегия → ⇄ Bollinger)\n"
        "Топик <b>сигналы</b>: <b>ОТКРЫТИЕ</b> → SL (новая M5 или TP, только ужесточение) → "
        "<b>ЗАКРЫТА</b> · результат в <b>R</b> (1R = |entry − SL|)\n"
        "Виртуальная сделка: TP1/TP2, трейл SL (BE → EMA20±ATR M5). Таймаута нет.\n"
        "Debug: <code>SCALP_ADVISOR_DEBUG_ENABLED</code> → "
        "<code>logs/scalp_advisor/SYMBOL.log</code> (шапка = актуальные условия).\n"
        "<code>/scalp_add</code> · <code>/scalp_tasks</code> → ⚙️ Стратегия → ✏️ Параметры · 📊 Уровни\n"
        "<code>SCALP_ADVISOR_ENABLED</code> · <code>SCALP_ADVISOR_DEBUG_VERBOSE</code>\n\n"
    )


def _atr_pullback_help_block(settings: Settings) -> str:
    enabled = "включён" if settings.atr_pullback_enabled else "выключен"
    return (
        "<b>ATR Pullback</b>\n"
        f"Мониторинг: <b>{enabled}</b> · шаг 1 на БТФ (кросс EMA + зона интереса) · "
        "шаг 2 на МТФ <b>1/5/15/30m</b> (подтягивание ≤1.5 ATR) · SL кросс−1 ATR · трейл slow−1 ATR\n"
        "В группу — только сигнал <b>входа</b> (шаг 2). Авто — только linear.\n"
        "Debug: <code>ATR_PULLBACK_DEBUG_ENABLED</code> → <code>logs/atr_pullback/SYMBOL.jsonl</code> "
        "(+ <code>.log</code> кратко).\n"
        "<code>/atr_add</code> · <code>/atr_tasks</code> · <code>ATR_PULLBACK_ENABLED</code>\n\n"
    )


def _sl_follow_help_block(settings: Settings) -> str:
    enabled = "включён" if settings.sl_follow_monitor_enabled else "выключен"
    return (
        "<b>Автоследование SL (Bybit)</b>\n"
        f"Мониторинг: <b>{enabled}</b> · :50 MSK · перенос SL на бирже\n"
        "На каждую открытую linear-позицию: ТФ базовый или младший, EMA из задания. "
        "Текущий SL с Bybit; расширение риска в $ — опция при включении.\n"
        "<code>/sl_follow</code> — мастер (с подтверждением)\n"
        "<code>/sl_follow_list</code> · <code>/sl_follow_stop SYMBOL</code>\n"
        "Топик <code>TELEGRAM_ALERTS_TOPIC_SL_FOLLOW</code> · "
        "<code>SL_FOLLOW_MONITOR_ENABLED</code>\n\n"
    )


def _price_spike_help_block(settings: Settings) -> str:
    enabled = "включён" if settings.price_spike_monitor_enabled else "выключен"
    return (
        "<b>Скачки цены (linear)</b>\n"
        f"Авто: <b>{enabled}</b> · :20 MSK · приоритет <b>ниже</b> сигналов EMA\n"
        "Позиции + <code>/watch_add</code> (алиас в алерте). Ход 1m vs средний за 60 мин; "
        "алерт: 🟢/🔴 от open 1m, ход в $ (USDT).\n"
        f"Порог <b>{settings.price_spike_ratio:g}×</b> · пауза алерта "
        f"<b>{settings.price_spike_alert_cooldown_min}</b> мин · топик "
        "<code>TELEGRAM_ALERTS_TOPIC_PRICE_SPIKE</code>\n"
        "<code>/watch_list</code> · <code>/watch_add</code> · <code>/watch_off|on|del</code>\n"
        "<code>PRICE_SPIKE_MONITOR_ENABLED</code> · <code>PRICE_SPIKE_RATIO</code> · "
        "<code>PRICE_SPIKE_ALERT_COOLDOWN_MIN</code>\n"
        "Вкл/выкл автоалертов: <code>/alerts</code>.\n\n"
    )


def _alerts_channel_block(settings: Settings) -> str:
    def _tid(v: int | None) -> str:
        return str(v) if v is not None else "—"

    return (
        "<b>Группа алертов</b>\n"
        "EMA / funding / скачки → топики. Из группы бот не читает. Команды — в личке.\n"
        f"<code>TELEGRAM_ALERTS_CHAT_ID</code>: {_tid(settings.telegram_alerts_chat_id)}\n"
        f"сигналы: {_tid(settings.telegram_alerts_topic_signals)} · "
        f"фандинг: {_tid(settings.telegram_alerts_topic_funding)} · "
        f"скачки: {_tid(settings.telegram_alerts_topic_price_spike)} · "
        f"SL EMA: {_tid(settings.telegram_alerts_topic_ema_sl)} · "
        f"pump: {_tid(settings.telegram_alerts_topic_pump)} · "
        f"dump: {_tid(settings.telegram_alerts_topic_dump)}\n\n"
    )


def _signals_help_block(settings: Settings) -> str:
    return (
        "<b>Сигналы EMA</b>\n"
        "Опрос 1 с (приоритет API). Сигнал на новой закрытой свече при кроссе EMA.\n"
        "ТФ: <b>5 · 15 · 30 · 60</b> мин. Вне расписания МСК — без отправки.\n"
        "Строки: <code>ℹ️</code> старший/младший ТФ; <code>SL …</code>; <code>⚠️</code> волатильность.\n"
        "<b>Изменение тренда</b> — перелом fast EMA всегда на <b>5m</b> (+ 2-я свеча): "
        "слабеет / усиливается относительно зоны 5m. СТФ/МТФ — по ТФ задания. "
        "<code>FAST_EMA_INFLECTION_ENABLED</code>\n"
        "<code>/zones</code> — зоны: сигнальный ТФ, <b>МТФ</b> младший, <b>СТФ</b> старший (синт.).\n"
        "<code>/sl</code> — SL базовый + младший ТФ по позициям и watch.\n"
        f"<code>ADVISOR_VOLATILITY_SPIKE_FACTOR={settings.advisor_volatility_spike_factor:g}</code>\n\n"
    )


def _tasks_summary_for_help(tasks: list[AdvisorTask]) -> str:
    if not tasks:
        return "<b>Задания EMA</b>: нет — <code>/task_add</code>\n\n"
    enabled = sum(1 for t in tasks if t.enabled)
    return (
        f"<b>Задания EMA</b>: {len(tasks)} в БД, включено {enabled}. "
        "Полный список: <code>/tasks</code> · <code>/status</code>\n\n"
    )


def _commands_help_block() -> str:
    return (
        "<b>Команды</b>\n"
        "/start /help /status · /task_add /tasks · /cancel\n"
        "/funding_scan /funding · /zones · /sl · /alerts\n"
        "/sl_follow · /sl_follow_list · /sl_follow_stop SYMBOL\n"
        "/atr_add · /atr_tasks\n"
        "/scalp_add · /scalp_tasks\n"
        "/pump · /pump_scan · /pump_tvh · /pump_fade · /pump_alarms · /pump_watches\n"
        "/watch_list /watch_add /watch_off /watch_on /watch_del · /id\n\n"
    )


def _split_help_chunks(text: str, max_len: int = _HELP_CHUNK_MAX) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    buf = ""
    for block in text.split("\n\n"):
        piece = block if not buf else f"{buf}\n\n{block}"
        if len(piece) <= max_len:
            buf = piece
            continue
        if buf:
            parts.append(buf)
        if len(block) <= max_len:
            buf = block
        else:
            for i in range(0, len(block), max_len):
                parts.append(block[i : i + max_len])
            buf = ""
    if buf:
        parts.append(buf)
    return parts


def build_help_parts(settings: Settings, tasks: list[AdvisorTask]) -> list[str]:
    """Части справки для Telegram (каждая &lt; 4096 символов)."""
    from app.bybit.instruments import market_label

    if settings.is_advisor_mode:
        chunks = [
            (
                "<b>Справка · советчик</b>\n\n"
                f"Bybit {settings.bybit_network} · {market_label(settings.bybit_category)}. "
                "Ордера не выставляются.\n\n"
                f"{_tasks_summary_for_help(tasks)}"
                f"{_alerts_channel_block(settings)}"
                f"{_signals_help_block(settings)}"
            ),
            (
                "<b>Справка · 2/3</b>\n\n"
                f"{_funding_help_block(settings)}"
                f"{_price_spike_help_block(settings)}"
                f"{_ema_sl_help_block(settings)}"
                f"{_sl_follow_help_block(settings)}"
                f"{_atr_pullback_help_block(settings)}"
                f"{_scalp_advisor_help_block(settings)}"
                f"{_pump_scan_help_block(settings)}"
            ),
            (
                "<b>Справка · 3/3</b>\n\n"
                f"{_commands_help_block()}"
                "<b>Задания</b> — PostgreSQL, мастер <code>/task_add</code>: "
                "тикер, EMA+ТФ (<code>9 21 5</code>), часы, псевдоним.\n\n"
                "<b>Доступ</b> — только Telegram id из таблицы <code>admins</code> "
                f"(супер-админ из .env: <code>{settings.superadmin_telegram_id}</code> "
                "добавляется при старте).\n"
                "<code>/id</code> — ваш id и статус доступа.\n"
                "<code>/admins</code> — список admins · "
                "<code>/admin_add</code> / <code>/admin_del</code> (только супер-админ .env).\n\n"
                f"<code>BOT_MODE={settings.bot_mode}</code> · "
                f"<code>BYBIT_NETWORK={settings.bybit_network}</code>\n"
            ),
        ]
    else:
        chunks = [
            (
                "<b>Справка · trading</b>\n\n"
                "Автоторговля Bybit / MT5.\n\n"
                f"{_commands_help_block()}"
                f"<code>BOT_MODE={settings.bot_mode}</code>\n"
            )
        ]

    out: list[str] = []
    for i, raw in enumerate(chunks, start=1):
        for piece in _split_help_chunks(raw.strip()):
            if len(chunks) > 1 and not piece.startswith("<b>Справка"):
                piece = f"<b>Справка · {i}/{len(chunks)}</b>\n\n{piece}"
            out.append(piece)
    return out


def build_help_text(settings: Settings, tasks: list[AdvisorTask]) -> str:
    """Одна строка (может превысить лимит TG — для совместимости)."""
    return "\n\n".join(build_help_parts(settings, tasks))


def _advisor_task_status_emoji(task: AdvisorTask) -> str:
    from app.trading_schedule import now_msk_in_windows

    if not task.enabled:
        return "⚪️"
    if now_msk_in_windows(task.trading_hours):
        return "🟢"
    return "🟡"


def _format_advisor_status_line(task: AdvisorTask) -> str:
    emoji = _advisor_task_status_emoji(task)
    tid = f"#{task.db_id} " if task.db_id else ""
    return (
        f"{emoji} {tid}<code>{task.display_name}</code> · {task.interval_label} · "
        f"EMA {task.ema_fast}/{task.ema_slow} · {task.hours_label()}"
    )


def build_status_text(settings: Settings, tasks: list[AdvisorTask]) -> str:
    lines = [
        f"<b>Статус</b> · режим <code>{settings.bot_mode}</code>",
        f"Bybit: <code>{settings.bybit_category}</code> / <code>{settings.bybit_network}</code>",
        "",
    ]
    if settings.is_advisor_mode:
        enabled = [t for t in tasks if t.enabled]
        if not tasks:
            lines.append("⚠️ Заданий EMA нет. Создайте: /task_add")
        else:
            lines.append(
                f"<b>Задания EMA ({len(tasks)}):</b> включено {len(enabled)}"
            )
            for t in tasks:
                lines.append(_format_advisor_status_line(t))
        lines.append("")
        lines.append("🟢 в расписании · 🟡 вне · ⚪️ выкл")
        lines.append("")
        if settings.telegram_signals_channel_ready:
            lines.append(
                "Алерты — в группу (топики .env). Здесь — настройка."
            )
        else:
            lines.append("Алерты в личку (задайте TELEGRAM_ALERTS_*). /help")
        lines.append(
            "Алерты SL / скачки / funding: <code>/alerts</code> или 🔔 в меню"
        )
    else:
        lines.append("Автоторговля: меню заданий.")
    text = "\n".join(lines)
    if len(text) > _HELP_CHUNK_MAX:
        return text[: _HELP_CHUNK_MAX - 20] + "\n… (см. /tasks)"
    return text
