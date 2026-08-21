"""Скальп-советник: вход, сопровождение SL, закрытие."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.bybit.rest import BybitRest, _interval_to_ms
from app.config import get_settings
from app.db.session import session_scope
from app.repository.scalp_advisor import (
    close_scalp_trade,
    fetch_enabled_scalp_tasks,
    open_scalp_trade,
    update_scalp_last_m1_bar,
    update_scalp_trade,
)
from app.scalp_advisor.logic import ScalpSignal, detect_scalp_signal
from app.scalp_advisor.tasks import (
    ScalpAdvisorTask,
    format_scalp_close,
    format_scalp_entry_message,
    format_scalp_sl_update,
    scalp_task_from_row,
)
from app.scalp_advisor.trade_manage import (
    TradeSnapshot,
    check_close,
    compute_trail_sl,
    sl_tightened,
    update_tp_flags,
)
from app.services.admin_notify import notify_signals_channel
from app.services.scalp_advisor_debug import (
    bar_time_msk,
    base_record,
    debug_enabled,
    write_record,
)
from app.trading_schedule import msk_datetime_in_windows

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

_M5_LIMIT = 120
_M1_LIMIT = 80
_SL_EPS = 1e-6


@dataclass
class TradeUpdate:
    sl: float
    tp1_hit: bool
    tp2_hit: bool
    last_reported_sl: float | None
    last_m5_sl_bar_ms: int | None


@dataclass
class TickOutcome:
    m1_bar_ms: int | None = None
    messages: list[str] = field(default_factory=list)
    trade_update: TradeUpdate | None = None
    open_sig: ScalpSignal | None = None
    should_close: bool = False


def _snap(task: ScalpAdvisorTask) -> TradeSnapshot:
    initial = task.initial_sl if task.initial_sl is not None else task.trade_sl
    return TradeSnapshot(
        side=task.trade_side or "Buy",
        entry=float(task.entry_price or 0),
        initial_sl=float(initial or 0),
        sl=float(task.trade_sl or 0),
        tp1=float(task.trade_tp1 or 0),
        tp2=float(task.trade_tp2 or 0),
        tp1_hit=task.tp1_hit,
        tp2_hit=task.tp2_hit,
    )


def _should_write_debug(
    *,
    decision: str,
    new_m1: bool,
    new_m5: bool,
    notify: bool,
) -> bool:
    if get_settings().scalp_advisor_debug_verbose:
        return True
    if decision not in ("noop", "wait", "same_m1", "hold"):
        return True
    if new_m1 or new_m5:
        return True
    if notify:
        return True
    return False


def _flush_debug(task: ScalpAdvisorTask, rec: dict[str, Any] | None, *, notify: bool) -> None:
    if rec is None or not debug_enabled():
        return
    rec["notify"] = notify
    if _should_write_debug(
        decision=rec.get("decision", "noop"),
        new_m1=bool((rec.get("m1") or {}).get("new_bar")),
        new_m5=bool((rec.get("m5") or {}).get("new_bar")),
        notify=notify,
    ):
        write_record(task.symbol, rec)


def _evaluate_sync(task: ScalpAdvisorTask) -> TickOutcome:
    out = TickOutcome()
    dbg: dict[str, Any] | None = base_record(task, kind="eval") if debug_enabled() else None

    def _finish() -> TickOutcome:
        _flush_debug(task, dbg, notify=bool(out.messages))
        return out

    if not task.levels or len(task.levels) < 2:
        log.warning("Scalp #%s %s: нужно ≥2 уровней", task.db_id, task.symbol)
        if dbg is not None:
            dbg["decision"] = "no_levels"
        return _finish()

    client = BybitRest(category="linear")
    mark = client.last_price(task.symbol)
    if mark is None:
        if dbg is not None:
            dbg["decision"] = "no_mark"
        return _finish()

    m1_bars = client.closed_ohlc_bars_with_ts(task.symbol, "1", limit=_M1_LIMIT)
    m5_bars = client.closed_ohlc_bars_with_ts(task.symbol, "5", limit=_M5_LIMIT)
    if not m1_bars or not m5_bars:
        if dbg is not None:
            dbg["decision"] = "no_bars"
            dbg["bars"] = {"m1": len(m1_bars or []), "m5": len(m5_bars or [])}
        return _finish()

    m1_open = m1_bars[-1][0]
    m5_open = m5_bars[-1][0]
    new_m1 = task.last_evaluated_m1_bar_ms != m1_open
    new_m5 = task.last_m5_sl_bar_ms != m5_open

    if dbg is not None:
        dbg["mark"] = round(mark, 8)
        dbg["m1"] = {
            "bar_open_ms": m1_open,
            "bar_time": bar_time_msk(m1_open),
            "new_bar": new_m1,
            "close": round(m1_bars[-1][4], 8),
        }
        dbg["m5"] = {
            "bar_open_ms": m5_open,
            "bar_time": bar_time_msk(m5_open),
            "new_bar": new_m5,
            "close": round(m5_bars[-1][4], 8),
        }

    if task.in_trade():
        if dbg is not None:
            dbg["kind"] = "manage"
        snap = _snap(task)
        close_ev = check_close(snap, mark)
        if close_ev is not None:
            if dbg is not None:
                dbg["decision"] = f"close_{close_ev.reason.lower()}"
                dbg["manage"] = {
                    "mark": round(mark, 8),
                    "exit": close_ev.exit_price,
                    "pnl_r": round(close_ev.pnl_r, 4),
                }
            out.messages.append(format_scalp_close(task, close_ev))
            out.should_close = True
            return _finish()

        tp1, tp2 = update_tp_flags(snap, mark)
        snap2 = TradeSnapshot(
            side=snap.side,
            entry=snap.entry,
            initial_sl=snap.initial_sl,
            sl=snap.sl,
            tp1=snap.tp1,
            tp2=snap.tp2,
            tp1_hit=tp1,
            tp2_hit=tp2,
        )
        new_sl = compute_trail_sl(snap2, m5_bars)
        tightened = sl_tightened(snap.side, snap.sl, new_sl)
        sl_changed = abs(new_sl - snap.sl) > _SL_EPS and tightened

        final_sl = new_sl if tightened else snap.sl
        reported = task.last_reported_sl

        tp1_new = tp1 and not task.tp1_hit
        tp2_new = tp2 and not task.tp2_hit

        if dbg is not None:
            dbg["manage"] = {
                "mark": round(mark, 8),
                "sl": round(final_sl, 8),
                "sl_prev": round(snap.sl, 8),
                "sl_changed": sl_changed,
                "tp1_hit": tp1,
                "tp2_hit": tp2,
                "tp1_new": tp1_new,
                "tp2_new": tp2_new,
            }

        if (new_m5 or tp1_new or tp2_new) and sl_changed:
            if dbg is not None:
                dbg["decision"] = "sl_update"
            out.messages.append(
                format_scalp_sl_update(
                    task,
                    reported,
                    final_sl,
                    tp1_hit=tp1,
                    tp2_hit=tp2,
                )
            )
            reported = final_sl
        elif dbg is not None:
            dbg["decision"] = "hold"

        out.trade_update = TradeUpdate(
            sl=final_sl,
            tp1_hit=tp1,
            tp2_hit=tp2,
            last_reported_sl=reported,
            last_m5_sl_bar_ms=m5_open if new_m5 else task.last_m5_sl_bar_ms,
        )
        return _finish()

    if not new_m1:
        if dbg is not None:
            dbg["decision"] = "same_m1"
        return _finish()

    m1_close_ms = m1_open + _interval_to_ms("1")
    close_dt = datetime.fromtimestamp(m1_close_ms / 1000, tz=MSK)
    in_hours = msk_datetime_in_windows(task.trading_hours, close_dt)
    if dbg is not None:
        dbg["in_trading_hours"] = in_hours
    if not in_hours:
        out.m1_bar_ms = m1_open
        if dbg is not None:
            dbg["decision"] = "outside_hours"
        return _finish()

    entry_dbg: dict[str, Any] = {}
    sig = detect_scalp_signal(
        m5_bars=m5_bars,
        m1_bars=m1_bars,
        levels=task.levels,
        symbol=task.symbol,
        m1_close_ms=m1_close_ms,
        cfg=task.strategy,
        debug_out=entry_dbg,
    )
    out.m1_bar_ms = m1_open
    if dbg is not None:
        dbg["entry"] = entry_dbg

    if sig is None:
        if dbg is not None:
            dbg["decision"] = "no_signal"
        return _finish()

    if dbg is not None:
        dbg["decision"] = "open"
        dbg["signal"] = {
            "side": sig.side,
            "entry": sig.entry,
            "sl": sig.sl,
            "tp1": sig.tp1,
            "tp2": sig.tp2,
            "pattern": sig.pattern,
        }

    out.open_sig = sig
    out.messages.append(format_scalp_entry_message(task, sig))
    return _finish()


async def run_scalp_advisor_tick() -> None:
    if not get_settings().scalp_advisor_enabled:
        return

    async with session_scope() as session:
        rows = await fetch_enabled_scalp_tasks(session)

    for row in rows:
        task = scalp_task_from_row(row)
        if task.db_id is None:
            continue
        try:
            outcome = await asyncio.to_thread(_evaluate_sync, task)

            if outcome.should_close:
                async with session_scope() as session:
                    await close_scalp_trade(session, task.db_id)

            elif outcome.trade_update is not None:
                u = outcome.trade_update
                async with session_scope() as session:
                    await update_scalp_trade(
                        session,
                        task.db_id,
                        sl=u.sl,
                        tp1_hit=u.tp1_hit,
                        tp2_hit=u.tp2_hit,
                        last_reported_sl=u.last_reported_sl,
                        last_m5_sl_bar_ms=u.last_m5_sl_bar_ms,
                    )

            elif outcome.open_sig is not None and outcome.m1_bar_ms is not None:
                sig = outcome.open_sig
                async with session_scope() as session:
                    await open_scalp_trade(
                        session,
                        task.db_id,
                        side=sig.side,
                        entry=sig.entry,
                        entry_ms=outcome.m1_bar_ms,
                        sl=sig.sl,
                        tp1=sig.tp1,
                        tp2=sig.tp2,
                        m1_bar_ms=outcome.m1_bar_ms,
                    )

            elif outcome.m1_bar_ms is not None and not task.in_trade():
                async with session_scope() as session:
                    await update_scalp_last_m1_bar(session, task.db_id, outcome.m1_bar_ms)

            for msg in outcome.messages:
                await notify_signals_channel(msg)
        except Exception:
            log.exception("scalp_advisor task %s", task.db_id)
