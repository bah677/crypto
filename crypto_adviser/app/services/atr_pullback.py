"""ATR Pullback: шаг 1/2, автовход, трейлинг SL."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.atr_pullback.logic import (
    cross_price_at_bar,
    detect_btf_cross,
    ema_at_index,
    in_interest_zone,
    initial_stop_loss,
    last_closed_bar_index_at,
    pullback_ready,
    trail_stop_loss,
)
from app.atr_pullback.tasks import (
    STATE_ARMED,
    STATE_IDLE,
    STATE_IN_POSITION,
    AtrPullbackTask,
    atr_pullback_task_from_row,
)
from app.bybit.rest import BybitRest, _interval_to_ms
from app.bybit.priority import try_begin_background_tick
from app.config import get_settings
from app.db.session import session_scope
from app.indicators.atr import robust_atr
from app.indicators.ema import ema_series
from app.repository.atr_pullback import (
    fetch_enabled_atr_pullback_tasks,
    update_atr_pullback_state,
)
from app.services.admin_notify import notify_signals_channel
from app.services.atr_pullback_debug import (
    base_record,
    debug_enabled,
    write_record,
    zone_snapshot,
    bar_time_msk,
)
from app.services.atr_pullback_entry import execute_entry
from app.services.sl_follow_logic import round_sl_price, should_update_stop_loss
from app.trading_schedule import msk_datetime_in_windows

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

_KLINE_LIMIT = 200
_MIN_BARS = 40


@dataclass
class TaskStateUpdate:
    state: str | None = None
    armed_side: str | None = None
    armed_at_ms: int | None = None
    btf_cross_bar_open_ms: int | None = None
    cross_price: float | None = None
    last_evaluated_btf_bar_ms: int | None = None
    last_evaluated_mtf_bar_ms: int | None = None
    last_sl_update_ms: int | None = None
    clear_armed: bool = False


@dataclass(frozen=True)
class AtrPullbackEvent:
    message: str


@dataclass
class EvalResult:
    updates: TaskStateUpdate | None
    events: list[AtrPullbackEvent]


def _side_label(side: str) -> str:
    return "LONG" if side == "Buy" else "SHORT"


def _zones_ok(
    task: AtrPullbackTask,
    *,
    side: str,
    btf_bars: list,
    mtf_bars: list,
    btf_idx: int,
    mtf_idx: int,
) -> bool:
    btf_closes = [b[4] for b in btf_bars]
    mtf_closes = [b[4] for b in mtf_bars]
    b_fast = ema_series(btf_closes, task.ema_fast)
    b_slow = ema_series(btf_closes, task.ema_slow)
    m_fast = ema_series(mtf_closes, task.ema_fast)
    m_slow = ema_series(mtf_closes, task.ema_slow)
    return in_interest_zone(side, btf_closes[btf_idx], b_fast, b_slow, btf_idx) and in_interest_zone(
        side, mtf_closes[mtf_idx], m_fast, m_slow, mtf_idx
    )


def _state_after(task: AtrPullbackTask, upd: TaskStateUpdate | None) -> str:
    if upd is None:
        return task.state
    if upd.clear_armed and upd.state is None:
        return STATE_IDLE
    return upd.state or task.state


def _should_write_eval_debug(
    *,
    new_btf: bool,
    new_mtf: bool,
    upd: TaskStateUpdate | None,
    events: list[AtrPullbackEvent],
    decision: str,
) -> bool:
    if get_settings().atr_pullback_debug_verbose:
        return True
    if decision not in ("noop", "wait"):
        return True
    if new_btf or new_mtf:
        return True
    if events:
        return True
    if upd and (upd.clear_armed or upd.state is not None):
        return True
    return False


def _flush_eval_debug(
    task: AtrPullbackTask,
    rec: dict[str, Any],
    *,
    upd: TaskStateUpdate | None,
    events: list[AtrPullbackEvent],
    new_btf: bool,
    new_mtf: bool,
) -> None:
    if not debug_enabled():
        return
    rec["state_after"] = _state_after(task, upd)
    rec["notify"] = bool(events)
    if _should_write_eval_debug(
        new_btf=new_btf,
        new_mtf=new_mtf,
        upd=upd,
        events=events,
        decision=rec.get("decision", "noop"),
    ):
        write_record(task.symbol, rec)


def _evaluate_task_sync(task: AtrPullbackTask) -> EvalResult:
    if task.db_id is None:
        return EvalResult(None, [])

    client = BybitRest(category="linear")
    events: list[AtrPullbackEvent] = []
    upd: TaskStateUpdate | None = None
    new_btf = False
    new_mtf = False
    dbg: dict[str, Any] | None = base_record(task, kind="eval") if debug_enabled() else None

    def _finish() -> EvalResult:
        result = EvalResult(upd, events)
        if dbg is not None:
            _flush_eval_debug(
                task, dbg, upd=upd, events=events, new_btf=new_btf, new_mtf=new_mtf
            )
        return result

    btf_bars = client.closed_ohlc_bars_with_ts(
        task.symbol, task.btf_interval, limit=_KLINE_LIMIT
    )
    mtf_bars = client.closed_ohlc_bars_with_ts(
        task.symbol, task.mtf_interval, limit=_KLINE_LIMIT
    )
    if len(btf_bars) < _MIN_BARS or len(mtf_bars) < _MIN_BARS:
        if dbg is not None:
            dbg["decision"] = "insufficient_bars"
            dbg["bars"] = {"btf": len(btf_bars), "mtf": len(mtf_bars), "need": _MIN_BARS}
            write_record(task.symbol, dbg)
        return EvalResult(None, [])

    btf_idx = len(btf_bars) - 1
    mtf_idx = len(mtf_bars) - 1
    btf_open = btf_bars[btf_idx][0]
    mtf_open = mtf_bars[mtf_idx][0]
    btf_step = _interval_to_ms(task.btf_interval)
    mtf_step = _interval_to_ms(task.mtf_interval)
    btf_close_ms = btf_open + btf_step
    mtf_close_ms = mtf_open + mtf_step

    btf_closes = [b[4] for b in btf_bars]
    mtf_closes = [b[4] for b in mtf_bars]
    name = task.display_name()
    tf = task.tf_pair_label()

    if dbg is not None:
        dbg["btf"] = {
            "bar_open_ms": btf_open,
            "bar_time": bar_time_msk(btf_open),
            "new_bar": task.last_evaluated_btf_bar_ms != btf_open,
            "close": round(btf_closes[btf_idx], 8),
        }
        dbg["mtf"] = {
            "bar_open_ms": mtf_open,
            "bar_time": bar_time_msk(mtf_open),
            "new_bar": task.last_evaluated_mtf_bar_ms != mtf_open,
            "close": round(mtf_closes[mtf_idx], 8),
        }

    # Ручной вход при сигналах без автоторговли
    if task.state == STATE_ARMED and task.armed_side:
        pos_side, pos_qty = client.get_open_position_side_qty(task.symbol)
        if pos_side == task.armed_side and float(pos_qty or 0) > 0:
            upd = TaskStateUpdate(state=STATE_IN_POSITION)
            if dbg is not None:
                dbg["decision"] = "manual_position_detected"
                dbg["position"] = {"side": pos_side, "qty": pos_qty}
            return _finish()

    # Позиция закрыта вне биржи
    if task.state == STATE_IN_POSITION and task.armed_side:
        pos_side, pos_qty = client.get_open_position_side_qty(task.symbol)
        if pos_side is None or float(pos_qty or 0) <= 0 or pos_side != task.armed_side:
            upd = TaskStateUpdate(state=STATE_IDLE, clear_armed=True)
            if dbg is not None:
                dbg["decision"] = "position_closed"
                dbg["position"] = {"side": pos_side, "qty": pos_qty}
            return _finish()

    # --- BTF tick ---
    new_btf = task.last_evaluated_btf_bar_ms != btf_open
    if dbg is not None and dbg.get("btf"):
        dbg["btf"]["new_bar"] = new_btf

    if new_btf:
        close_dt = datetime.fromtimestamp(btf_close_ms / 1000, tz=MSK)
        in_hours = msk_datetime_in_windows(task.trading_hours, close_dt)
        mtf_at = last_closed_bar_index_at(mtf_bars, btf_close_ms, task.mtf_interval)
        cross = detect_btf_cross(btf_closes, task.ema_fast, task.ema_slow) if in_hours else None
        zones_for_cross = (
            _zones_ok(
                task,
                side=cross,
                btf_bars=btf_bars,
                mtf_bars=mtf_bars,
                btf_idx=btf_idx,
                mtf_idx=mtf_at,
            )
            if cross and mtf_at is not None
            else False
        )

        if dbg is not None:
            dbg["btf"].update(
                {
                    "in_trading_hours": in_hours,
                    "cross": cross,
                    "mtf_align_idx": mtf_at,
                    "zones_ok_for_cross": zones_for_cross,
                    "zone": zone_snapshot(
                        btf_closes,
                        btf_idx,
                        task.ema_fast,
                        task.ema_slow,
                        cross or task.armed_side,
                    ),
                }
            )
            if mtf_at is not None:
                dbg["btf"]["mtf_zone_at_cross"] = zone_snapshot(
                    mtf_closes,
                    mtf_at,
                    task.ema_fast,
                    task.ema_slow,
                    cross or task.armed_side,
                )

        if cross and mtf_at is not None and zones_for_cross:
            cp = cross_price_at_bar(btf_closes, btf_idx, task.ema_fast, task.ema_slow)
            upd = TaskStateUpdate(
                state=STATE_ARMED,
                armed_side=cross,
                armed_at_ms=btf_close_ms,
                btf_cross_bar_open_ms=btf_open,
                cross_price=cp,
                last_evaluated_btf_bar_ms=btf_open,
            )
            if dbg is not None:
                prev = task.armed_side
                if prev and prev != cross:
                    dbg["decision"] = "step1_reverse"
                elif task.state != STATE_ARMED or prev != cross:
                    dbg["decision"] = "step1_arm"
                else:
                    dbg["decision"] = "step1_refresh"
                dbg["cross_price"] = cp
        elif task.state == STATE_ARMED and task.armed_side and in_hours:
            mtf_chk = mtf_at if mtf_at is not None else mtf_idx
            zones_armed = _zones_ok(
                task,
                side=task.armed_side,
                btf_bars=btf_bars,
                mtf_bars=mtf_bars,
                btf_idx=btf_idx,
                mtf_idx=mtf_chk,
            )
            if dbg is not None:
                dbg["btf"]["zone_armed"] = zone_snapshot(
                    btf_closes,
                    btf_idx,
                    task.ema_fast,
                    task.ema_slow,
                    task.armed_side,
                )
                dbg["btf"]["zones_ok_armed"] = zones_armed
            if not zones_armed:
                upd = TaskStateUpdate(
                    state=STATE_IDLE,
                    clear_armed=True,
                    last_evaluated_btf_bar_ms=btf_open,
                )
                if dbg is not None:
                    dbg["decision"] = "step1_disarm_btf_zone"
            else:
                upd = TaskStateUpdate(
                    state=STATE_ARMED,
                    last_evaluated_btf_bar_ms=btf_open,
                )
                if dbg is not None:
                    dbg["decision"] = "step1_hold"
        else:
            upd = TaskStateUpdate(last_evaluated_btf_bar_ms=btf_open)
            if dbg is not None and dbg.get("decision") is None:
                dbg["decision"] = "btf_cursor" if not in_hours else "btf_no_cross"

    # --- MTF tick: шаг 2 ---
    if task.state == STATE_ARMED and task.armed_side and task.cross_price is not None:
        new_mtf = task.last_evaluated_mtf_bar_ms != mtf_open
        if dbg is not None and dbg.get("mtf"):
            dbg["mtf"]["new_bar"] = new_mtf

        if new_mtf:
            mtf_dt = datetime.fromtimestamp(mtf_close_ms / 1000, tz=MSK)
            in_hours = msk_datetime_in_windows(task.trading_hours, mtf_dt)
            side = task.armed_side
            btf_at = last_closed_bar_index_at(btf_bars, mtf_close_ms, task.btf_interval)
            if btf_at is None:
                btf_at = btf_idx

            zones_step2 = _zones_ok(
                task,
                side=side,
                btf_bars=btf_bars,
                mtf_bars=mtf_bars,
                btf_idx=btf_at,
                mtf_idx=mtf_idx,
            )
            atr = robust_atr(mtf_bars)
            fast_v, slow_v = ema_at_index(
                mtf_closes, mtf_idx, task.ema_fast, task.ema_slow
            )
            close = mtf_closes[mtf_idx]
            dist_atr = abs(close - fast_v) / atr if atr and fast_v is not None else None
            pb_ok = (
                bool(atr and fast_v is not None and pullback_ready(side, close, fast_v, atr))
            )

            if dbg is not None:
                dbg["mtf"].update(
                    {
                        "in_trading_hours": in_hours,
                        "atr": round(atr, 8) if atr else None,
                        "ema_fast": round(fast_v, 8) if fast_v is not None else None,
                        "ema_slow": round(slow_v, 8) if slow_v is not None else None,
                        "pullback_dist_atr": round(dist_atr, 4) if dist_atr else None,
                        "pullback_ok": pb_ok,
                        "pullback_max_atr": 1.5,
                        "zone": zone_snapshot(
                            mtf_closes,
                            mtf_idx,
                            task.ema_fast,
                            task.ema_slow,
                            side,
                        ),
                        "btf_zone_at_mtf": zone_snapshot(
                            btf_closes,
                            btf_at,
                            task.ema_fast,
                            task.ema_slow,
                            side,
                        ),
                        "zones_ok_step2": zones_step2,
                        "cross_price": task.cross_price,
                    }
                )

            if in_hours and not zones_step2:
                mtf_upd = TaskStateUpdate(
                    state=STATE_IDLE,
                    clear_armed=True,
                    last_evaluated_mtf_bar_ms=mtf_open,
                )
                upd = mtf_upd if upd is None else mtf_upd
                if dbg is not None:
                    dbg["decision"] = "step1_disarm_mtf_zone"
            elif in_hours:
                if pb_ok:
                    sl = initial_stop_loss(side, task.cross_price, atr)  # type: ignore[arg-type]
                    mark = client.last_price(task.symbol) or close
                    msg = (
                        f"<b>ATR Pullback · Шаг 2 · ВХОД</b> · {name} · {tf}\n"
                        f"{_side_label(side)} · dist fast = {dist_atr:.2f} ATR\n"
                        f"Цена {close:g} · SL {sl:g}\n"
                        f"МТФ {bar_time_msk(mtf_open)}"
                    )
                    entered = False
                    entry_detail = None
                    if task.auto_trade:
                        ok, detail = execute_entry(
                            task,
                            client=client,
                            side=side,
                            sl_price=sl,
                            mark_price=mark,
                        )
                        msg += f"\n{'✅' if ok else '⚠️'} {detail}"
                        entered = ok
                        entry_detail = detail
                    if task.auto_trade and entered:
                        mtf_upd = TaskStateUpdate(
                            state=STATE_IN_POSITION,
                            last_evaluated_mtf_bar_ms=mtf_open,
                        )
                    else:
                        mtf_upd = TaskStateUpdate(
                            last_evaluated_mtf_bar_ms=mtf_open,
                        )
                    upd = mtf_upd if upd is None else mtf_upd
                    events.append(AtrPullbackEvent(msg))
                    if dbg is not None:
                        dbg["decision"] = "step2_entry"
                        dbg["entry"] = {
                            "side": side,
                            "sl": round(sl, 8),
                            "mark": round(mark, 8),
                            "auto_trade": task.auto_trade,
                            "entered": entered,
                            "detail": entry_detail,
                        }
                else:
                    mtf_upd = TaskStateUpdate(last_evaluated_mtf_bar_ms=mtf_open)
                    if upd is None:
                        upd = mtf_upd
                    elif upd.last_evaluated_mtf_bar_ms is None:
                        upd.last_evaluated_mtf_bar_ms = mtf_open
                    if dbg is not None and dbg.get("decision") is None:
                        dbg["decision"] = "step2_wait_pullback"
            elif dbg is not None and dbg.get("decision") is None:
                dbg["decision"] = "mtf_outside_hours"

    if dbg is not None and dbg.get("decision") is None:
        dbg["decision"] = "noop"

    return _finish()


def _trail_task_sync(task: AtrPullbackTask) -> EvalResult:
    if task.db_id is None or task.state != STATE_IN_POSITION or not task.armed_side:
        return EvalResult(None, [])

    client = BybitRest(category="linear")
    dbg: dict[str, Any] | None = base_record(task, kind="trail") if debug_enabled() else None

    pos = client.get_linear_position_snapshot(task.symbol)
    if pos is None or pos.side != task.armed_side:
        upd = TaskStateUpdate(state=STATE_IDLE, clear_armed=True)
        if dbg is not None:
            dbg["decision"] = "trail_position_gone"
            dbg["state_after"] = STATE_IDLE
            write_record(task.symbol, dbg)
        return EvalResult(upd, [])

    mtf_bars = client.closed_ohlc_bars_with_ts(
        task.symbol, task.mtf_interval, limit=_KLINE_LIMIT
    )
    if len(mtf_bars) < _MIN_BARS:
        return EvalResult(None, [])

    mtf_idx = len(mtf_bars) - 1
    mtf_open = mtf_bars[mtf_idx][0]
    if task.last_sl_update_ms == mtf_open:
        if dbg is not None and get_settings().atr_pullback_debug_verbose:
            dbg["decision"] = "trail_already_processed"
            dbg["mtf"] = {"bar_open_ms": mtf_open}
            dbg["state_after"] = task.state
            write_record(task.symbol, dbg)
        return EvalResult(None, [])

    mtf_closes = [b[4] for b in mtf_bars]
    atr = robust_atr(mtf_bars)
    _, slow_v = ema_at_index(mtf_closes, mtf_idx, task.ema_fast, task.ema_slow)
    if not atr or slow_v is None:
        return EvalResult(None, [])

    new_sl = trail_stop_loss(task.armed_side, slow_v, atr)
    ok, reason = should_update_stop_loss(pos, new_sl, allow_sl_widen=False)
    if dbg is not None:
        dbg["mtf"] = {
            "bar_open_ms": mtf_open,
            "bar_time": bar_time_msk(mtf_open),
            "atr": round(atr, 8),
            "ema_slow": round(slow_v, 8),
            "trail_sl": round(new_sl, 8),
        }
        dbg["position"] = {
            "sl": pos.stop_loss,
            "mark": pos.mark_price,
            "side": pos.side,
        }

    if not ok:
        upd = TaskStateUpdate(last_sl_update_ms=mtf_open)
        if dbg is not None:
            dbg["decision"] = "trail_skip"
            dbg["trail_reason"] = reason
            dbg["state_after"] = task.state
            write_record(task.symbol, dbg)
        return EvalResult(upd, [])

    sl_str = round_sl_price(client, task.symbol, new_sl)
    try:
        client.set_position_stop_loss(task.symbol, sl_str)
    except Exception as e:
        log.exception("atr trail SL %s %s", task.symbol, e)
        if dbg is not None:
            dbg["decision"] = "trail_error"
            dbg["error"] = str(e)
            write_record(task.symbol, dbg)
        return EvalResult(None, [])

    log.info(
        "ATR Pullback trail %s: SL %s → %s (%s)",
        task.symbol,
        pos.stop_loss,
        sl_str,
        reason,
    )
    upd = TaskStateUpdate(last_sl_update_ms=mtf_open)
    if dbg is not None:
        dbg["decision"] = "trail_updated"
        dbg["trail_reason"] = reason
        dbg["sl_new"] = sl_str
        dbg["state_after"] = task.state
        write_record(task.symbol, dbg)
    return EvalResult(upd, [])


async def _apply_update(task_id: int, upd: TaskStateUpdate) -> None:
    async with session_scope() as session:
        await update_atr_pullback_state(
            session,
            task_id,
            state=upd.state,
            armed_side=upd.armed_side,
            armed_at_ms=upd.armed_at_ms,
            btf_cross_bar_open_ms=upd.btf_cross_bar_open_ms,
            cross_price=upd.cross_price,
            last_evaluated_btf_bar_ms=upd.last_evaluated_btf_bar_ms,
            last_evaluated_mtf_bar_ms=upd.last_evaluated_mtf_bar_ms,
            last_sl_update_ms=upd.last_sl_update_ms,
            clear_armed=upd.clear_armed,
        )


async def _notify_events(events: list[AtrPullbackEvent]) -> None:
    """В группу — только сообщения о входе (шаг 2)."""
    for ev in events:
        if not ev.message.strip():
            continue
        try:
            await notify_signals_channel(ev.message)
        except Exception:
            log.exception("atr_pullback notify")


async def run_atr_pullback_tick() -> None:
    if not get_settings().atr_pullback_enabled:
        return

    async with session_scope() as session:
        rows = await fetch_enabled_atr_pullback_tasks(session)

    tasks = [atr_pullback_task_from_row(r) for r in rows]
    if not tasks:
        return

    for task in tasks:
        try:
            result = await asyncio.to_thread(_evaluate_task_sync, task)
            if result.updates:
                state = result.updates.state or task.state
                await _apply_update(
                    task.db_id,  # type: ignore[arg-type]
                    TaskStateUpdate(
                        state=state,
                        armed_side=result.updates.armed_side or task.armed_side,
                        armed_at_ms=result.updates.armed_at_ms or task.armed_at_ms,
                        btf_cross_bar_open_ms=(
                            result.updates.btf_cross_bar_open_ms
                            or task.btf_cross_bar_open_ms
                        ),
                        cross_price=result.updates.cross_price or task.cross_price,
                        last_evaluated_btf_bar_ms=(
                            result.updates.last_evaluated_btf_bar_ms
                            or task.last_evaluated_btf_bar_ms
                        ),
                        last_evaluated_mtf_bar_ms=(
                            result.updates.last_evaluated_mtf_bar_ms
                            or task.last_evaluated_mtf_bar_ms
                        ),
                        last_sl_update_ms=result.updates.last_sl_update_ms,
                        clear_armed=result.updates.clear_armed,
                    ),
                )
            await _notify_events(result.events)
        except Exception:
            log.exception("atr_pullback task %s", task.db_id)


async def run_atr_pullback_trail_tick() -> None:
    if not get_settings().atr_pullback_enabled:
        return
    if not try_begin_background_tick("atr_pullback_trail"):
        return

    try:
        async with session_scope() as session:
            rows = await fetch_enabled_atr_pullback_tasks(session)
        tasks = [
            atr_pullback_task_from_row(r)
            for r in rows
            if (r.state or "") == STATE_IN_POSITION
        ]
        for task in tasks:
            try:
                result = await asyncio.to_thread(_trail_task_sync, task)
                if result.updates:
                    await _apply_update(
                        task.db_id,  # type: ignore[arg-type]
                        result.updates,
                    )
                await _notify_events(result.events)
            except Exception:
                log.exception("atr_pullback trail %s", task.db_id)
    finally:
        from app.bybit.priority import end_background_tick

        end_background_tick()
