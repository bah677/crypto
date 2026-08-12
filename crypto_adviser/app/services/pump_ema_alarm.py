"""Мониторинг пересечения цены и EMA (будильник pump)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.bybit.priority import background_request_scope, end_background_tick, try_begin_background_tick
from app.bybit.rest import BybitRest
from app.config import get_settings
from app.db.session import session_scope
from app.pump_scan.ema_levels import ema_price_from_map, fetch_ema_map_sync, price_side
from app.pump_scan.weekly_ema import format_ema_entry_label
from app.repository.pump_ema_alarms import (
    fetch_active_pump_ema_alarms,
    update_pump_ema_alarm_state,
)
from app.services.admin_notify import _send_message

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


def _crossed(prev_side: str | None, new_side: str, direction: str) -> str | None:
    if not prev_side or prev_side == new_side:
        return None
    if direction in ("down", "both") and prev_side == "above" and new_side == "below":
        return "down"
    if direction in ("up", "both") and prev_side == "below" and new_side == "above":
        return "up"
    return None


def _format_trigger(
    *,
    symbol: str,
    ema_key: str,
    ema_value: float,
    price: float,
    cross: str,
) -> str:
    ema_lbl = format_ema_entry_label(ema_key)
    cross_lbl = "⬇️ сверху вниз" if cross == "down" else "⬆️ снизу вверх"
    return (
        f"🔔 <b>EMA будильник</b> · <code>{symbol}</code>\n"
        f"Пересечение {cross_lbl}: <b>{ema_lbl}</b>\n"
        f"Цена <code>{price:.5g}</code> · EMA <code>{ema_value:.5g}</code>"
    )


async def run_pump_ema_alarm_tick() -> None:
    if not get_settings().pump_ema_alarm_enabled:
        return
    if not await asyncio.to_thread(try_begin_background_tick, "pump_ema_alarm"):
        log.debug("EMA alarm tick skipped — фон занят (pump scan)")
        return
    try:
        await _run_alarm_tick_async()
    finally:
        await asyncio.to_thread(end_background_tick)


async def _run_alarm_tick_async() -> None:
    async with session_scope() as session:
        alarms = await fetch_active_pump_ema_alarms(session)
    if not alarms:
        return

    client = BybitRest(category="linear")
    now = datetime.now(tz=MSK)
    ema_cache: dict[str, dict[str, float | None]] = {}

    for alarm in alarms:
        sym = alarm.symbol.upper()
        try:
            ema_val, price = await asyncio.to_thread(
                _fetch_price_and_ema, client, sym, alarm.ema_key, ema_cache
            )
        except Exception:
            log.exception("EMA alarm check failed %s #%s", sym, alarm.id)
            continue
        if ema_val is None or price is None:
            continue

        side = price_side(price, ema_val)
        cross = _crossed(alarm.last_side, side, alarm.direction)

        async with session_scope() as session:
            await update_pump_ema_alarm_state(
                session,
                alarm.id,
                last_side=side,
                last_ema_value=ema_val,
                checked_at=now,
                triggered=cross is not None,
            )
            await session.commit()

        if cross:
            msg = _format_trigger(
                symbol=sym,
                ema_key=alarm.ema_key,
                ema_value=ema_val,
                price=price,
                cross=cross,
            )
            try:
                await _send_message(alarm.telegram_chat_id, msg)
            except Exception:
                log.exception("EMA alarm notify failed #%s", alarm.id)


def _fetch_price_and_ema(
    client: BybitRest,
    symbol: str,
    ema_key: str,
    cache: dict[str, dict[str, float | None]],
) -> tuple[float | None, float | None]:
    with background_request_scope():
        if symbol not in cache:
            cache[symbol] = fetch_ema_map_sync(client, symbol)
        ema_val = ema_price_from_map(cache[symbol], ema_key)
        if ema_val is None:
            return None, None
        price = client.last_price(symbol)
        if not price or price <= 0:
            return None, None
        return ema_val, float(price)
