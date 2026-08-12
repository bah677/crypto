"""Режим «советчик»: EMA-кросс на Bybit, сигнал в личку Telegram, без ордеров."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.advisor.intervals import ALLOWED_KLINE_INTERVALS
from app.advisor.mtf import (
    format_mtf_lines,
    junior_zone_at_signal,
    senior_zone_at_signal,
)
from app.advisor.tasks import AdvisorTask, advisor_task_from_row
from app.bybit.rest import BybitRest, _interval_to_ms
from app.config import get_settings
from app.indicators.ema import crossed_bearish, crossed_bullish, ema_series
from app.indicators.volatility import last_two_candles_high_volatility
from app.advisor.fast_ema_inflection import detect_fast_ema_inflection
from app.services.ema_sl_levels import (
    LowerBarsCache,
    format_sl_values_line,
    sl_pair_at_bar,
)
from app.repository.advisor_tasks import (
    fetch_enabled_advisor_tasks,
    update_last_evaluated_bar_open,
)
from app.trading_schedule import msk_datetime_in_windows

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

_last_error_notify_ts: float = 0.0
_notified_errors: set[str] = set()
_valid_symbols: set[tuple[str, str]] = set()

# Сколько пропущенных свечей максимум догонять за один тик (защита от лавины после долгого простоя)
_MAX_CATCHUP_BARS = 120
# Пауза после закрытия свечи перед опросом (мс)
_POLL_SLACK_MS = 300
_KLINE_LIMIT = 150


@dataclass(frozen=True)
class AdvisorTickResult:
    task_id: int
    task_key: str
    open_ms: int
    message: str | None = None


def _format_bar_time_msk(open_ms: int) -> str:
    dt = datetime.fromtimestamp(open_ms / 1000, tz=MSK)
    return dt.strftime("%Y-%m-%d %H:%M MSK")


def _bar_close_dt(open_ms: int, step_ms: int) -> datetime:
    return datetime.fromtimestamp((open_ms + step_ms) / 1000, tz=MSK)


def _fresh_limit_ms(interval: str) -> int:
    """Окно «свежести» только для последней свечи в тике (не при догоне)."""
    step = _interval_to_ms(interval)
    return max(10_000, int(step * 0.35))


def _task_due(prev_cursor: int | None, interval: str, now_ms: int) -> bool:
    """Пора ли опрашивать Bybit: прошло закрытие свечи после курсора."""
    if prev_cursor is None:
        return True
    step_ms = _interval_to_ms(interval)
    return now_ms >= prev_cursor + step_ms + _POLL_SLACK_MS


def _ensure_symbol(category: str, symbol: str) -> None:
    key = (category, symbol.upper())
    if key in _valid_symbols:
        return
    BybitRest(category=category).instrument_filters(symbol)
    _valid_symbols.add(key)


def _indices_to_process(
    bars: list[tuple[int, float, float, float, float]], prev_cursor: int | None
) -> list[int]:
    if not bars:
        return []
    if prev_cursor is None:
        return [len(bars) - 1]

    pending = [i for i, bar in enumerate(bars) if bar[0] > prev_cursor]
    if not pending:
        return []
    if len(pending) > _MAX_CATCHUP_BARS:
        log.warning(
            "Догон обрезан до %s свечей (в очереди %s)",
            _MAX_CATCHUP_BARS,
            len(pending),
        )
        pending = pending[-_MAX_CATCHUP_BARS:]
    return pending


_VOLATILITY_WARNING = "⚠️ Внимание высокая волатильность"


def _signal_for_bar(
    task: AdvisorTask,
    closes: list[float],
    ohlc_bars: list[tuple[int, float, float, float, float]],
    bar_index: int,
    open_ms: int,
    *,
    client: BybitRest,
    lower_bars_cache: LowerBarsCache,
) -> AdvisorTickResult | None:
    need = max(task.ema_fast, task.ema_slow) + 5
    if bar_index + 1 < need:
        return None

    slice_closes = closes[: bar_index + 1]
    fast = ema_series(slice_closes, task.ema_fast)
    slow = ema_series(slice_closes, task.ema_slow)
    bull = crossed_bullish(fast, slow)
    bear = crossed_bearish(fast, slow)
    if not bull and not bear:
        return AdvisorTickResult(
            task_id=task.db_id,
            task_key=task.key,
            open_ms=open_ms,
            message=None,
        )

    step_ms = _interval_to_ms(task.interval)
    close_dt = _bar_close_dt(open_ms, step_ms)
    if not msk_datetime_in_windows(task.trading_hours, close_dt):
        return AdvisorTickResult(
            task_id=task.db_id,
            task_key=task.key,
            open_ms=open_ms,
            message=None,
        )

    if bull and not bear:
        emoji, side_ru = "🟢", "Покупка"
    elif bear and not bull:
        emoji, side_ru = "🔴", "Продажа"
    else:
        log.warning("Советчик %s: bull и bear одновременно — пропуск", task.key)
        return AdvisorTickResult(
            task_id=task.db_id,
            task_key=task.key,
            open_ms=open_ms,
            message=None,
        )

    bar_label = _format_bar_time_msk(open_ms)
    senior = senior_zone_at_signal(
        ohlc_bars, bar_index, task.interval, task.ema_fast, task.ema_slow
    )
    junior = junior_zone_at_signal(
        task.interval,
        ohlc_bars,
        bar_index,
        open_ms,
        task.ema_fast,
        task.ema_slow,
        client,
        task.symbol,
        lower_bars_cache,
    )
    mtf_lines = format_mtf_lines(senior, junior)
    extra: list[str] = list(mtf_lines) if mtf_lines else []
    try:
        base_sl, _, mtf_sl, _ = sl_pair_at_bar(
            task,
            client,
            ohlc_bars,
            lower_bars_cache,
            bar_index=bar_index,
        )
        sl_line = format_sl_values_line(task, base_sl, mtf_sl)
        if sl_line:
            extra.append(sl_line)
    except Exception:
        log.warning(
            "Советчик %s: SL в сигнале не рассчитан",
            task.key,
            exc_info=True,
        )
    message = task.format_signal_message(
        emoji=emoji,
        side_ru=side_ru,
        bar_label=bar_label,
        extra_lines=extra or None,
    )
    ohlc_slice = [(o, h, l, c) for _, o, h, l, c in ohlc_bars[: bar_index + 1]]
    if last_two_candles_high_volatility(
        ohlc_slice, factor=get_settings().advisor_volatility_spike_factor
    ):
        message = f"{message}\n{_VOLATILITY_WARNING}"
    return AdvisorTickResult(
        task_id=task.db_id,
        task_key=task.key,
        open_ms=open_ms,
        message=message,
    )


def _evaluate_task(
    task: AdvisorTask,
    prev_cursor: int | None,
) -> list[AdvisorTickResult]:
    if task.db_id is None:
        raise RuntimeError(f"Задание {task.key} без id в БД")

    now_ms = int(time.time() * 1000)
    if not _task_due(prev_cursor, task.interval, now_ms):
        return []

    try:
        _ensure_symbol(task.bybit_category, task.symbol)
    except Exception as e:
        raise RuntimeError(
            f"{task.symbol}: недоступен в Bybit ({task.bybit_category}): {e}"
        ) from e

    need_bars = max(task.ema_fast, task.ema_slow) + 35
    kline_limit = min(_KLINE_LIMIT, max(80, need_bars))
    client = BybitRest(category=task.bybit_category)
    lower_bars_cache: LowerBarsCache = {}
    bars = client.closed_ohlc_bars_with_ts(
        task.symbol, task.interval, limit=kline_limit
    )
    if not bars:
        return []

    indices = _indices_to_process(bars, prev_cursor)
    if not indices:
        return []

    if len(indices) > 1:
        first = _format_bar_time_msk(bars[indices[0]][0])
        last = _format_bar_time_msk(bars[indices[-1]][0])
        log.info(
            "Советчик %s: догон %s свечей (%s … %s)",
            task.key,
            len(indices),
            first,
            last,
        )

    closes = [c for _, _, _, _, c in bars]
    step_ms = _interval_to_ms(task.interval)
    fresh_limit = _fresh_limit_ms(task.interval)
    latest_idx = indices[-1]

    out: list[AdvisorTickResult] = []
    for idx in indices:
        open_ms = bars[idx][0]
        is_latest = idx == latest_idx
        if is_latest and prev_cursor is not None:
            age_ms = now_ms - (open_ms + step_ms)
            if age_ms >= fresh_limit:
                log.debug(
                    "Советчик %s: последняя свеча open=%s поздно (%sms) — только курсор",
                    task.key,
                    open_ms,
                    age_ms,
                )
                out.append(
                    AdvisorTickResult(
                        task_id=task.db_id,
                        task_key=task.key,
                        open_ms=open_ms,
                        message=None,
                    )
                )
                continue

        r = _signal_for_bar(
            task,
            closes,
            bars,
            idx,
            open_ms,
            client=client,
            lower_bars_cache=lower_bars_cache,
        )
        if r is not None:
            out.append(r)

        if is_latest and get_settings().fast_ema_inflection_enabled:
            bars_5m = client.closed_ohlc_bars_with_ts(task.symbol, "5", limit=5)
            if bars_5m:
                o5 = bars_5m[-1][0]
                close_5m_dt = _bar_close_dt(o5, _interval_to_ms("5"))
                if msk_datetime_in_windows(task.trading_hours, close_5m_dt):
                    hit = detect_fast_ema_inflection(
                        task,
                        client=client,
                        lower_bars_cache=lower_bars_cache,
                    )
                else:
                    hit = None
            else:
                hit = None
            if hit is not None:
                out.append(
                    AdvisorTickResult(
                        task_id=task.db_id,
                        task_key=task.key,
                        open_ms=hit.confirm_open_ms,
                        message=hit.message,
                    )
                )

    return out


def _evaluate_advisor_sync(
    tasks: list[AdvisorTask],
    cursors: dict[int, int | None],
) -> list[AdvisorTickResult]:
    if not tasks:
        return []

    out: list[AdvisorTickResult] = []
    for task in tasks:
        if task.db_id is None:
            continue
        try:
            chunk = _evaluate_task(task, cursors.get(task.db_id))
        except RuntimeError:
            raise
        except Exception as e:
            from pybit.exceptions import FailedRequestError

            if isinstance(e, FailedRequestError):
                log.warning(
                    "Советчик %s: лимит Bybit, пропуск тика (%s)",
                    task.key,
                    e,
                )
            else:
                log.exception("Советчик: сбой задания %s", task.key)
            continue
        out.extend(chunk)

    out.sort(key=lambda r: r.open_ms)
    return out


async def run_advisor_tick() -> None:
    from app.db.session import session_scope
    from app.services.admin_notify import notify_signals_channel, notify_superadmin

    if not get_settings().is_advisor_mode:
        return

    async with session_scope() as session:
        rows = await fetch_enabled_advisor_tasks(session)
        if not rows:
            return
        tasks = [advisor_task_from_row(r) for r in rows]
        cursors = {r.id: r.last_evaluated_bar_open_ms for r in rows}

    from app.bybit.priority import advisor_tick_scope

    def _run() -> list[AdvisorTickResult]:
        with advisor_tick_scope():
            return _evaluate_advisor_sync(tasks, cursors)

    try:
        results = await asyncio.to_thread(_run)
    except RuntimeError as e:
        log.error("Советчик: %s", e)
        err_key = str(e)
        global _last_error_notify_ts
        now = time.monotonic()
        if err_key not in _notified_errors or now - _last_error_notify_ts >= 300.0:
            _last_error_notify_ts = now
            _notified_errors.add(err_key)
            try:
                await notify_superadmin(f"⚠️ Советчик: {e}")
            except Exception:
                log.exception("Не удалось отправить уведомление")
        return
    except Exception:
        log.exception("Советчик: сбой тика")
        return

    if not results:
        return

    max_open_by_task: dict[int, int] = {}
    for r in results:
        max_open_by_task[r.task_id] = max(max_open_by_task.get(r.task_id, 0), r.open_ms)

    async with session_scope() as session:
        for r in results:
            if r.message:
                await notify_signals_channel(r.message, parse_mode=None)
                if r.message.startswith("Изменение тренда"):
                    log.info(
                        "Советчик: перелом fast EMA %s bar open=%s",
                        r.task_key,
                        r.open_ms,
                    )
                else:
                    log.info("Советчик: сигнал %s bar open=%s", r.task_key, r.open_ms)
        for task_id, open_ms in max_open_by_task.items():
            if cursors.get(task_id) != open_ms:
                await update_last_evaluated_bar_open(session, task_id, open_ms)
