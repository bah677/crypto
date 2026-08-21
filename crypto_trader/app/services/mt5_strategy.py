from __future__ import annotations

import asyncio
import logging
import time

from app.config import get_settings
from app.db.session import session_scope
from app.indicators.ema import crossed_bearish, crossed_bullish, ema_series
from app.mt5 import session as mt5_sess
from app.mt5.orders import (
    close_positions_market,
    get_position_side_volume,
    last_price,
    place_market_with_tp_sl,
    round_to_tick,
    round_volume,
    tick_size,
)
from app.mt5.rates import closed_bars_with_ts, interval_step_ms
from app.mt5.runtime import get_mt5
from app.mt5.symbol_marketwatch import ensure_symbol_selected
from app.repository.tasks import (
    fetch_enabled_mt5_tasks,
    set_task_enabled,
    update_last_evaluated_bar_open,
)
from app.services.strategy import (
    InstrumentChannelError,
    TaskSnapshot,
    _now_msk_in_windows,
    _snapshot,
    SIGNAL_FRESH_MS,
)

log = logging.getLogger(__name__)


def _evaluate_mt5_sync(snap: TaskSnapshot) -> int | None:
    MT5 = get_mt5()

    if snap.trading_channel != "mt5":
        return None

    tinfo = MT5.terminal_info()
    if tinfo is None or not getattr(tinfo, "connected", False):
        return None

    if not snap.level_prices:
        log.warning("Задание %s без уровней — пропуск", snap.id)
        return None

    if not ensure_symbol_selected(MT5, snap.symbol):
        raise InstrumentChannelError(
            snap.id,
            snap.symbol,
            repr(MT5.last_error()),
            last_bar_open_ms=None,
        )

    try:
        tick_f = tick_size(MT5, snap.symbol)
    except Exception as e:
        raise InstrumentChannelError(
            snap.id, snap.symbol, repr(e), last_bar_open_ms=None
        ) from e

    try:
        bars = closed_bars_with_ts(MT5, snap.symbol, snap.kline_interval, limit=500)
    except ValueError as e:
        raise InstrumentChannelError(
            snap.id, snap.symbol, repr(e), last_bar_open_ms=None
        ) from e

    if not bars:
        return None

    last_open = bars[-1][0]
    if (
        snap.last_evaluated_bar_open_ms is not None
        and last_open == snap.last_evaluated_bar_open_ms
    ):
        return None

    step_ms = interval_step_ms(snap.kline_interval)
    now_ms = int(time.time() * 1000)
    bar_close_ms = last_open + step_ms
    age_ms = now_ms - bar_close_ms

    if snap.last_evaluated_bar_open_ms is not None and age_ms >= SIGNAL_FRESH_MS:
        log.debug(
            "Задание %s (MT5): свеча open=%s обработана слишком поздно (%sms ≥ %s) — только курсор",
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
            "Задание %s (MT5): мало свечей для EMA (нужно ≥%s, есть %s)",
            snap.id,
            need,
            len(closes),
        )
        return None

    if not _now_msk_in_windows(snap.trading_hours):
        return last_open

    fast = ema_series(closes, snap.ema_fast)
    slow = ema_series(closes, snap.ema_slow)
    bull = crossed_bullish(fast, slow)
    bear = crossed_bearish(fast, slow)
    if not bull and not bear:
        return last_open

    close_px = closes[-1]
    band = snap.delta_ticks * tick_f
    if not any(abs(close_px - lv) <= band for lv in snap.level_prices):
        return last_open

    desired: str | None = None
    if bull and not bear:
        desired = "Buy"
    elif bear and not bull:
        desired = "Sell"
    else:
        log.warning("Задание %s (MT5): одновременно bull и bear — пропуск", snap.id)
        return last_open

    assert desired is not None
    magic = get_settings().mt5_magic
    pos_side, pos_qty = get_position_side_volume(MT5, snap.symbol, magic)
    if pos_side and pos_qty > 0:
        if pos_side == desired:
            log.debug(
                "Задание %s (MT5): уже позиция %s — без добавления",
                snap.id,
                pos_side,
            )
            return last_open
        if not close_positions_market(MT5, snap.symbol, magic):
            log.error(
                "Задание %s (MT5): не удалось закрыть противоположную позицию",
                snap.id,
            )
            return last_open
        time.sleep(0.35)

    last = last_price(MT5, snap.symbol)
    if last is None:
        last = close_px

    vol = round_volume(MT5, snap.symbol, float(snap.order_qty))

    sl_px: float | None = None
    if desired == "Buy":
        tp_raw = last + snap.take_profit_ticks * tick_f
        if snap.stop_loss_ticks > 0:
            sl_raw = last - snap.stop_loss_ticks * tick_f
            sl_px = round_to_tick(sl_raw, tick_f)
    else:
        tp_raw = last - snap.take_profit_ticks * tick_f
        if snap.stop_loss_ticks > 0:
            sl_raw = last + snap.stop_loss_ticks * tick_f
            sl_px = round_to_tick(sl_raw, tick_f)

    tp = round_to_tick(tp_raw, tick_f)
    log.info(
        "Сигнал MT5 задание=%s %s %s close_last=%s last=%s tp=%s sl=%s vol=%s",
        snap.id,
        snap.symbol,
        desired,
        close_px,
        last,
        tp,
        sl_px if sl_px is not None else "—",
        vol,
    )
    try:
        place_market_with_tp_sl(MT5, snap.symbol, desired, vol, tp, sl_px)
    except Exception:
        log.exception("Ошибка ордера MT5 задание=%s", snap.id)

    return last_open


async def run_mt5_strategy_tick() -> None:
    from app.mt5 import runtime
    from app.services.admin_notify import notify_superadmin

    async with session_scope() as session:
        tasks = await fetch_enabled_mt5_tasks(session)
        snapshots = [_snapshot(t) for t in tasks]
    if not snapshots:
        return

    tr = mt5_sess.mt5_transport()
    if tr == "linux_bridge":
        if not mt5_sess.mt5linux_import_ok():
            mt5_sess.warn_mt5_linux_bridge_misconfigured()
            return
    elif tr == "local":
        if not mt5_sess.native_mt5_import_ok():
            mt5_sess.warn_mt5_tasks_no_native_package()
            return
    else:
        log.error("Неизвестный MT5_TRANSPORT=%r", tr)
        return

    if not runtime.mt5_runtime_initialized() or not mt5_sess.mt5_connected():
        if tr == "linux_bridge":
            mt5_sess.warn_mt5_linux_bridge_misconfigured()
        else:
            mt5_sess.warn_mt5_tasks_without_terminal()
        return

    for snap in snapshots:
        try:
            new_ts = await asyncio.to_thread(_evaluate_mt5_sync, snap)
            if new_ts is not None:
                async with session_scope() as session:
                    await update_last_evaluated_bar_open(session, snap.id, new_ts)
        except InstrumentChannelError as e:
            log.error(
                "Задание %s: символ %s недоступен в MT5 — отключаем",
                e.task_id,
                e.symbol,
            )
            async with session_scope() as session:
                await set_task_enabled(session, e.task_id, False)
                if e.last_bar_open_ms is not None:
                    await update_last_evaluated_bar_open(
                        session, e.task_id, e.last_bar_open_ms
                    )
            try:
                await notify_superadmin(
                    f"Задание #{e.task_id} выключено: символ {e.symbol} недоступен в канале MT5 "
                    f"(имя как в Market Watch, символ включён, рынок открыт). Подстановок нет.\n{e.detail}"
                )
            except Exception:
                log.exception("Не удалось отправить уведомление в Telegram")
        except Exception:
            log.exception("Сбой задания MT5 id=%s", snap.id)
