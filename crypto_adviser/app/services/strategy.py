from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.bybit.rest import BybitRest, _interval_to_ms
from app.db.session import session_scope
from app.indicators.ema import crossed_bearish, crossed_bullish, ema_series
from app.repository.tasks import fetch_enabled_tasks, update_last_evaluated_bar_open

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

# После фактического закрытия свечи: окно, в котором допускается вход (≈ первая секунда + сеть).
SIGNAL_FRESH_MS = 2500


class InstrumentChannelError(Exception):
    """Тикер недоступен в выбранном канале (Bybit API / MT5) — задание нужно остановить."""

    def __init__(
        self,
        task_id: int,
        symbol: str,
        detail: str,
        *,
        last_bar_open_ms: int | None = None,
    ) -> None:
        self.task_id = task_id
        self.symbol = symbol
        self.detail = detail
        self.last_bar_open_ms = last_bar_open_ms
        super().__init__(f"task={task_id} symbol={symbol} {detail}")


@dataclass(frozen=True)
class TaskSnapshot:
    id: int
    symbol: str
    trading_channel: str
    ema_fast: int
    ema_slow: int
    kline_interval: str
    delta_ticks: int
    take_profit_ticks: int
    stop_loss_ticks: int
    order_qty: str
    trading_hours: list[dict[str, str]]
    level_prices: list[float]
    last_evaluated_bar_open_ms: int | None


def _snapshot(task) -> TaskSnapshot:
    return TaskSnapshot(
        id=task.id,
        symbol=task.symbol,
        trading_channel=getattr(task, "trading_channel", "bybit_v5") or "bybit_v5",
        ema_fast=task.ema_fast,
        ema_slow=task.ema_slow,
        kline_interval=task.kline_interval,
        delta_ticks=task.delta_ticks,
        take_profit_ticks=task.take_profit_ticks,
        stop_loss_ticks=task.stop_loss_ticks,
        order_qty=task.order_qty,
        trading_hours=task.trading_hours(),
        level_prices=[float(x.price) for x in task.levels],
        last_evaluated_bar_open_ms=task.last_evaluated_bar_open_ms,
    )


def _parse_hhmm(s: str) -> tuple[int, int]:
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Неверное время: {s}")
    return int(parts[0]), int(parts[1])


def _time_to_sec(h: int, m: int, sec: int = 0) -> int:
    return h * 3600 + m * 60 + sec


def _now_msk_in_windows(windows: list[dict[str, str]]) -> bool:
    from app.trading_schedule import now_msk_in_windows

    return now_msk_in_windows(windows)


def _evaluate_sync(snap: TaskSnapshot) -> int | None:
    """
    Один проход по заданию. Возвращает openTime (мс) последней закрытой свечи, которую
    нужно зафиксировать в БД, либо None если состояние свечи не менялось.
    """
    if not snap.level_prices:
        log.warning("Задание %s без уровней — пропуск", snap.id)
        return None

    if snap.trading_channel != "bybit_v5":
        return None

    client = BybitRest()
    try:
        tick_size, qty_step = client.instrument_filters(snap.symbol)
    except Exception as e:
        raise InstrumentChannelError(
            snap.id, snap.symbol, repr(e), last_bar_open_ms=None
        ) from e

    bars = client.closed_bars_with_ts(snap.symbol, snap.kline_interval, limit=500)
    if not bars:
        return None

    last_open = bars[-1][0]
    if (
        snap.last_evaluated_bar_open_ms is not None
        and last_open == snap.last_evaluated_bar_open_ms
    ):
        return None

    step_ms = _interval_to_ms(snap.kline_interval)
    now_ms = int(time.time() * 1000)
    bar_close_ms = last_open + step_ms
    age_ms = now_ms - bar_close_ms

    if snap.last_evaluated_bar_open_ms is not None and age_ms >= SIGNAL_FRESH_MS:
        log.debug(
            "Задание %s: свеча open=%s обработана слишком поздно (%sms ≥ %s) — только курсор",
            snap.id,
            last_open,
            age_ms,
            SIGNAL_FRESH_MS,
        )
        return last_open

    closes = [c for _, c in bars]
    need = max(snap.ema_fast, snap.ema_slow) + 5
    if len(closes) < need:
        log.warning(
            "Задание %s: мало свечей для EMA (нужно ≥%s, есть %s)",
            snap.id,
            need,
            len(closes),
        )
        return None

    if not _now_msk_in_windows(snap.trading_hours):
        return last_open

    fast = ema_series(closes, snap.ema_fast)
    slow = ema_series(closes, snap.ema_slow)
    # Пересечение **на закрытии последней закрытой свечи**:
    # быстрая EMA снизу вверх через медленную → лонг; сверху вниз → шорт.
    bull = crossed_bullish(fast, slow)
    bear = crossed_bearish(fast, slow)
    if not bull and not bear:
        return last_open

    close_px = closes[-1]
    tick_f = float(tick_size)
    band = snap.delta_ticks * tick_f
    if not any(abs(close_px - lv) <= band for lv in snap.level_prices):
        return last_open

    desired: str | None = None
    if bull and not bear:
        desired = "Buy"
    elif bear and not bull:
        desired = "Sell"
    else:
        log.warning("Задание %s: одновременно bull и bear — пропуск", snap.id)
        return last_open

    pos_side, pos_qty = client.get_open_position_side_qty(snap.symbol)
    if pos_side and float(pos_qty or 0) > 0:
        if pos_side == desired:
            log.debug(
                "Задание %s: уже позиция %s — новый сигнал в ту же сторону, без добавления",
                snap.id,
                pos_side,
            )
            return last_open
        if client.category in ("linear", "inverse"):
            close_side = "Sell" if pos_side == "Buy" else "Buy"
            try:
                client.place_reduce_only_market(snap.symbol, close_side, pos_qty)
                time.sleep(0.35)
            except Exception:
                log.exception(
                    "Задание %s: не удалось закрыть противоположную позицию", snap.id
                )
                return last_open
        else:
            log.warning(
                "Задание %s: spot — автозакрытие при сигнале в другую сторону не реализовано; "
                "открытие может не пройти, если уже есть позиция/баланс",
                snap.id,
            )

    last = client.last_price(snap.symbol)
    if last is None:
        last = close_px

    qty = BybitRest.round_qty(snap.order_qty, qty_step)
    sl_str: str | None = None
    if desired == "Buy":
        tp_raw = last + snap.take_profit_ticks * tick_f
        if snap.stop_loss_ticks > 0:
            sl_raw = last - snap.stop_loss_ticks * tick_f
            sl_str = BybitRest.round_to_tick(sl_raw, tick_size)
    else:
        tp_raw = last - snap.take_profit_ticks * tick_f
        if snap.stop_loss_ticks > 0:
            sl_raw = last + snap.stop_loss_ticks * tick_f
            sl_str = BybitRest.round_to_tick(sl_raw, tick_size)

    tp = BybitRest.round_to_tick(tp_raw, tick_size)
    log.info(
        "Сигнал задание=%s %s %s close_last=%s last=%s tp=%s sl=%s qty=%s",
        snap.id,
        snap.symbol,
        desired,
        close_px,
        last,
        tp,
        sl_str or "—",
        qty,
    )
    try:
        client.place_market_with_tp_sl(snap.symbol, desired, qty, tp, sl_str)
    except Exception:
        log.exception("Ошибка ордера задание=%s", snap.id)

    return last_open


async def run_bybit_strategy_tick() -> None:
    from app.config import get_settings
    from app.repository.tasks import set_task_enabled
    from app.services.admin_notify import notify_superadmin

    async with session_scope() as session:
        tasks = await fetch_enabled_tasks(session)
        snapshots = [_snapshot(t) for t in tasks]
    cat = get_settings().bybit_category
    for snap in snapshots:
        try:
            new_ts = await asyncio.to_thread(_evaluate_sync, snap)
            if new_ts is not None:
                async with session_scope() as session:
                    await update_last_evaluated_bar_open(session, snap.id, new_ts)
        except InstrumentChannelError as e:
            log.error(
                "Задание %s: тикер %s не найден в Bybit API (%s) — отключаем",
                e.task_id,
                e.symbol,
                cat,
            )
            async with session_scope() as session:
                await set_task_enabled(session, e.task_id, False)
                if e.last_bar_open_ms is not None:
                    await update_last_evaluated_bar_open(
                        session, e.task_id, e.last_bar_open_ms
                    )
            try:
                await notify_superadmin(
                    f"Задание #{e.task_id} выключено: тикер {e.symbol} не найден "
                    f"в канале Bybit API (category={cat!r}). Подстановки тикеров запрещены.\n{e.detail}"
                )
            except Exception:
                log.exception("Не удалось отправить уведомление в Telegram")
        except Exception:
            log.exception("Сбой задания id=%s", snap.id)


async def run_strategy_tick() -> None:
    from app.config import get_settings

    if get_settings().is_advisor_mode:
        from app.services.advisor import run_advisor_tick
        from app.services.atr_pullback import run_atr_pullback_tick

        await run_advisor_tick()
        await run_atr_pullback_tick()
        from app.services.scalp_advisor import run_scalp_advisor_tick

        await run_scalp_advisor_tick()
        return

    await run_bybit_strategy_tick()
    from app.services import mt5_strategy as _m5

    await _m5.run_mt5_strategy_tick()
