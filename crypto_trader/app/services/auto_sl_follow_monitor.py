"""Мониторинг автоследования SL: новая свеча → расчёт → set_trading_stop на Bybit."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.advisor.tasks import AdvisorTask, advisor_task_from_row
from app.bybit.rest import BybitRest
from app.config import get_settings
from app.db.models import SlFollowRow
from app.repository.advisor_tasks import get_advisor_task
from app.repository.sl_follow import (
    disable_sl_follow,
    fetch_enabled_sl_follow,
    update_sl_follow_bar_cursor,
)
from app.services.admin_notify import notify_sl_follow_channel
from app.services.ema_sl_levels import (
    LowerBarsCache,
    SlTfMode,
    follow_tf_interval,
    follow_tf_label,
    sl_price_for_tf_mode,
)
from app.services.sl_follow_logic import (
    format_move_report,
    round_sl_price,
    should_update_stop_loss,
)

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")
_KLINE_LIMIT = 150


def _process_follow_row_sync(
    follow: SlFollowRow,
    task: AdvisorTask,
) -> tuple[list[str], int | None, bool]:
    """
    (отчёты, open_ms новой свечи для курсора, disable_follow).
    """
    reports: list[str] = []
    client = BybitRest(category=task.bybit_category or "linear")
    pos = client.get_linear_position_snapshot(follow.symbol)
    if pos is None:
        return (
            [f"⚠️ <code>{follow.symbol}</code>: позиция закрыта — автоследование выключено"],
            None,
            True,
        )

    if pos.side != follow.position_side:
        return (
            [
                f"⚠️ <code>{follow.symbol}</code>: сторона {pos.side}, "
                f"ожидали {follow.position_side} — автоследование выключено"
            ],
            None,
            True,
        )

    mode: SlTfMode = "junior" if follow.sl_tf_mode == "junior" else "base"
    tf_interval = follow_tf_interval(task, mode)
    tf_label = follow_tf_label(task, mode)
    need = max(task.ema_fast, task.ema_slow) + 5
    limit = min(_KLINE_LIMIT, max(80, need + 20))

    base_bars = client.closed_ohlc_bars_with_ts(
        task.symbol, task.interval, limit=limit
    )
    if not base_bars:
        return ([f"⚠️ <code>{follow.symbol}</code>: нет свечей для расчёта SL"], None, False)

    follow_bars = (
        base_bars
        if tf_interval == task.interval
        else client.closed_ohlc_bars_with_ts(task.symbol, tf_interval, limit=limit)
    )
    if not follow_bars:
        return ([f"⚠️ <code>{follow.symbol}</code>: нет свечей {tf_label}"], None, False)

    bar_open = follow_bars[-1][0]
    if follow.last_processed_bar_open_ms == bar_open:
        return ([], None, False)

    lower_cache: LowerBarsCache = {}
    bar_index = len(base_bars) - 1
    new_sl, err = sl_price_for_tf_mode(
        task, client, base_bars, lower_cache, mode, bar_index=bar_index
    )
    if new_sl is None:
        reports.append(
            format_move_report(
                follow.symbol,
                tf_label,
                pos.stop_loss,
                0.0,
                f"не рассчитан SL: {err or '—'}",
                skipped=True,
            )
        )
        return reports, bar_open, False

    ok, reason = should_update_stop_loss(
        pos, new_sl, allow_sl_widen=follow.allow_sl_widen
    )
    if not ok:
        reports.append(
            format_move_report(
                follow.symbol,
                tf_label,
                pos.stop_loss,
                new_sl,
                reason,
                skipped=True,
            )
        )
        return reports, bar_open, False

    sl_str = round_sl_price(client, follow.symbol, new_sl)
    try:
        client.set_position_stop_loss(follow.symbol, sl_str)
    except Exception as e:
        reports.append(
            f"⚠️ <code>{follow.symbol}</code>: не удалось поставить SL {sl_str}: {e}"
        )
        return reports, bar_open, False

    reports.append(
        format_move_report(
            follow.symbol,
            tf_label,
            pos.stop_loss,
            float(sl_str),
            reason,
        )
    )
    return reports, bar_open, False


async def run_sl_follow_tick() -> None:
    from app.bybit.priority import end_background_tick, try_begin_background_tick
    from app.db.session import session_scope

    s = get_settings()
    if not s.sl_follow_monitor_enabled:
        return

    if not await asyncio.to_thread(try_begin_background_tick, "sl_follow"):
        log.info("SL follow: пропуск тика — занят советчик или другой фоновый опрос")
        return

    try:
        async with session_scope() as session:
            rows = await fetch_enabled_sl_follow(session)

        if not rows:
            log.info(
                "SL follow: нет активных правил — включите через /sl_follow"
            )
            return

        all_reports: list[str] = []

        for follow in rows:
            async with session_scope() as session:
                task_row = await get_advisor_task(session, follow.advisor_task_id)
            if not task_row or not task_row.enabled:
                all_reports.append(
                    f"⚠️ <code>{follow.symbol}</code>: задание EMA выкл/удалено — стоп follow"
                )
                async with session_scope() as session:
                    await disable_sl_follow(session, follow.symbol)
                continue

            task = advisor_task_from_row(task_row)
            try:
                reports, bar_open, disable = await asyncio.to_thread(
                    _process_follow_row_sync, follow, task
                )
            except Exception:
                log.exception("SL follow: %s", follow.symbol)
                all_reports.append(f"⚠️ <code>{follow.symbol}</code>: ошибка тика")
                continue

            if reports:
                all_reports.extend(reports)
            if disable:
                async with session_scope() as session:
                    await disable_sl_follow(session, follow.symbol)
            elif bar_open is not None:
                async with session_scope() as session:
                    await update_sl_follow_bar_cursor(session, follow.id, bar_open)

        if not all_reports:
            log.info(
                "SL follow: тик без событий (%s правил — нет новой свечи ТФ или перенос не нужен)",
                len(rows),
            )
            return

        now = datetime.now(tz=MSK).strftime("%H:%M MSK")
        body = f"<b>Автоследование SL</b> · {now}\n\n" + "\n\n".join(all_reports[:12])
        await notify_sl_follow_channel(body)
        log.info("SL follow: отчёт (%s событий)", len(all_reports))
    except Exception:
        log.exception("SL follow: сбой тика")
    finally:
        await asyncio.to_thread(end_background_tick)
