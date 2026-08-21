"""SL = цена закрытия следующей свечи, при которой пересекутся EMA (базовый и младший ТФ)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.advisor.tasks import AdvisorTask
from app.bybit.rest import BybitRest
from app.config import get_settings
from app.services.ema_sl_levels import (
    LowerBarsCache,
    format_sl_report_line,
    sl_pair_at_bar,
)
from app.services.monitored_symbols import collect_monitored_symbols_sync

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

_KLINE_LIMIT = 150
_last_sl_bar_ms: dict[int, int] = {}


def _display_label(task: AdvisorTask, watch_labels: dict[str, str]) -> str:
    alias = (task.alias or "").strip()
    if alias:
        return alias
    return watch_labels.get(task.symbol.upper(), task.symbol)


def build_sl_report_sync(
    tasks: list[AdvisorTask],
    watch: list[tuple[str, str]],
    *,
    only_on_new_bar: bool,
    for_command: bool = False,
) -> tuple[str | None, list[str]]:
    monitored = collect_monitored_symbols_sync(watch)
    if not monitored:
        return None, []

    enabled = [t for t in tasks if t.enabled and t.symbol.upper() in monitored]
    if not enabled:
        return None, ["нет включённых заданий по открытым позициям / watch"]

    from app.bybit.priority import background_request_scope

    lines: list[str] = []
    errors: list[str] = []
    by_cat: dict[str, list[AdvisorTask]] = {}
    for t in enabled:
        by_cat.setdefault(t.bybit_category or "linear", []).append(t)

    if for_command:
        _build_sl_lines(by_cat, monitored, lines, errors, only_on_new_bar=only_on_new_bar)
    else:
        with background_request_scope():
            _build_sl_lines(
                by_cat, monitored, lines, errors, only_on_new_bar=only_on_new_bar
            )

    if not lines:
        return None, errors

    now = datetime.now(tz=MSK).strftime("%H:%M MSK")
    body = f"<b>Уровни SL (EMA cross)</b> · {now}\n\n" + "\n".join(lines)
    if errors:
        body += "\n\n⚠️ " + "\n".join(errors[:6])
    return body, errors


def _build_sl_lines(
    by_cat: dict[str, list[AdvisorTask]],
    monitored: dict[str, str],
    lines: list[str],
    errors: list[str],
    *,
    only_on_new_bar: bool,
) -> None:
    for category, chunk in sorted(by_cat.items()):
        client = BybitRest(category=category)
        lower_cache: LowerBarsCache = {}
        for task in chunk:
            if task.db_id is None:
                continue
            need = max(task.ema_fast, task.ema_slow) + 5
            limit = min(_KLINE_LIMIT, max(80, need + 20))
            label = _display_label(task, monitored)
            try:
                bars = client.closed_ohlc_bars_with_ts(
                    task.symbol, task.interval, limit=limit
                )
            except Exception as e:
                errors.append(f"{label}: {e}")
                continue
            if not bars:
                errors.append(f"{label}: нет свечей")
                continue
            bar_open = bars[-1][0]
            if only_on_new_bar and _last_sl_bar_ms.get(task.db_id) == bar_open:
                continue
            try:
                base_sl, base_err, mtf_sl, mtf_err = sl_pair_at_bar(
                    task, client, bars, lower_cache
                )
            except Exception as e:
                errors.append(f"{label}: {e}")
                continue
            if base_sl is None and mtf_sl is None:
                errors.append(
                    f"{label}: база {base_err or '—'}; младший {mtf_err or '—'}"
                )
                continue
            lines.append(format_sl_report_line(label, task, base_sl, mtf_sl))
            if only_on_new_bar:
                _last_sl_bar_ms[task.db_id] = bar_open


async def run_ema_sl_tick() -> None:
    from app.advisor.tasks import advisor_task_from_row
    from app.bybit.priority import end_background_tick, try_begin_background_tick
    from app.db.session import session_scope
    from app.repository.advisor_tasks import fetch_enabled_advisor_tasks
    from app.repository.price_watch import fetch_enabled_price_watch
    from app.services.admin_notify import notify_ema_sl_channel

    from app.services.alert_toggles import ema_sl_reports_active

    if not await ema_sl_reports_active():
        return

    if not await asyncio.to_thread(try_begin_background_tick, "ema_sl"):
        log.info("EMA SL: пропуск тика — занят советчик или другой фоновый опрос")
        return

    try:
        async with session_scope() as session:
            rows = await fetch_enabled_advisor_tasks(session)
            watch_rows = await fetch_enabled_price_watch(session)
        tasks = [advisor_task_from_row(r) for r in rows]
        watch = [(r.symbol, r.alias or "") for r in watch_rows]
        report, _ = await asyncio.to_thread(
            build_sl_report_sync, tasks, watch, only_on_new_bar=True
        )
    except Exception:
        log.exception("EMA SL: сбой тика")
        return
    finally:
        await asyncio.to_thread(end_background_tick)

    if not report:
        log.info("EMA SL: без отчёта (нет новой свечи / позиций+watch / все задания уже обработаны)")
        return
    try:
        await notify_ema_sl_channel(report)
        log.info("EMA SL: отчёт в топик")
    except Exception:
        log.exception("EMA SL: не отправили отчёт")


async def run_ema_sl_command() -> str:
    """Отчёт в личку по команде /sl."""
    from app.advisor.tasks import advisor_task_from_row
    from app.db.session import session_scope
    from app.repository.advisor_tasks import fetch_enabled_advisor_tasks
    from app.repository.price_watch import fetch_enabled_price_watch

    async with session_scope() as session:
        rows = await fetch_enabled_advisor_tasks(session)
        watch_rows = await fetch_enabled_price_watch(session)
    tasks = [advisor_task_from_row(r) for r in rows]
    watch = [(r.symbol, r.alias or "") for r in watch_rows]

    report, errors = await asyncio.to_thread(
        build_sl_report_sync,
        tasks,
        watch,
        only_on_new_bar=False,
        for_command=True,
    )
    if report:
        return report
    if errors:
        return "⚠️ " + "\n".join(errors[:10])
    return "Нет данных: откройте позиции или <code>/watch_add</code>, включите задания EMA."
