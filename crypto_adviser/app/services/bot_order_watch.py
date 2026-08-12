"""Мониторинг ордеров бота на Bybit → reply в Telegram при изменении статуса."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.bybit.rest import BybitRest
from app.db.session import session_scope
from app.repository.bot_order_watches import (
    fetch_active_bot_order_watches,
    update_bot_order_watch_state,
)
from app.services.admin_notify import reply_to_chat_message

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

_TERMINAL_STATUSES = frozenset(
    {
        "Filled",
        "Cancelled",
        "Rejected",
        "Deactivated",
        "PartiallyFilledCanceled",
    }
)
_MAX_MISS = 5


def _order_field(order: dict, key: str, default: str = "") -> str:
    val = order.get(key)
    if val is None:
        return default
    return str(val).strip()


def _format_order_update(
    *,
    symbol: str,
    side: str,
    order_type: str,
    old_status: str,
    new_status: str,
    qty: str,
    price: str,
    cum_exec_qty: str,
    avg_price: str,
    reject_reason: str,
) -> str | None:
    sym = f"<code>{symbol}</code>"
    base = f"{sym} · {side} · {order_type}"

    if new_status == "Filled":
        line = f"✅ <b>Ордер исполнен</b>\n{base}"
        if cum_exec_qty:
            line += f"\nИсполнено: <b>{cum_exec_qty}</b>"
            if avg_price:
                line += f" @ <b>{avg_price}</b>"
        return line

    if new_status == "PartiallyFilled":
        line = f"🟡 <b>Частичное исполнение</b>\n{base}"
        line += f"\nИсполнено: <b>{cum_exec_qty}</b> / {qty}"
        if avg_price:
            line += f" · ср. <b>{avg_price}</b>"
        return line

    if new_status == "Cancelled":
        return f"❌ <b>Ордер отменён</b>\n{base}"

    if new_status == "Rejected":
        reason = reject_reason or "неизвестно"
        return f"⛔ <b>Ордер отклонён</b>\n{base}\nПричина: {reason}"

    if new_status in ("Deactivated", "PartiallyFilledCanceled"):
        return f"⚪ <b>Ордер закрыт</b> ({new_status})\n{base}"

    if new_status != old_status:
        extra = f"\nЦена: <b>{price}</b> · qty <b>{qty}</b>" if price else ""
        return f"ℹ️ Статус ордера: <b>{old_status}</b> → <b>{new_status}</b>\n{base}{extra}"

    if cum_exec_qty and cum_exec_qty not in ("0", "0.0", ""):
        line = f"🟡 <b>Исполнение обновлено</b>\n{base}"
        line += f"\nИсполнено: <b>{cum_exec_qty}</b> / {qty}"
        if avg_price:
            line += f" · ср. <b>{avg_price}</b>"
        return line

    return None


def _check_watch_sync(watch) -> tuple[str | None, dict | None, bool, int]:
    """
  Returns: (telegram_text | None, order_dict | None, still_active, miss_count)
    """
    client = BybitRest(category="linear")
    order = client.get_linear_order(watch.symbol, watch.bybit_order_id)
    if order is None:
        miss = int(watch.miss_count or 0) + 1
        if miss >= _MAX_MISS:
            return (
                f"⚠️ Ордер <code>{watch.symbol}</code> "
                f"<code>{watch.bybit_order_id[:8]}…</code> не найден на Bybit "
                f"(возможно отменён или истёк). Мониторинг остановлен.",
                None,
                False,
                miss,
            )
        return (None, None, True, miss)

    status = _order_field(order, "orderStatus", watch.order_status)
    cum_qty = _order_field(order, "cumExecQty", "0")
    avg_px = _order_field(order, "avgPrice")
    reject = _order_field(order, "rejectReason")

    changed = (
        status != watch.order_status
        or cum_qty != (watch.cum_exec_qty or "0")
        or (avg_px and avg_px != (watch.avg_price or ""))
    )
    if not changed:
        return (None, order, status not in _TERMINAL_STATUSES, 0)

    text = _format_order_update(
        symbol=watch.symbol,
        side=watch.side,
        order_type=watch.order_type,
        old_status=watch.order_status,
        new_status=status,
        qty=watch.qty,
        price=watch.price,
        cum_exec_qty=cum_qty,
        avg_price=avg_px,
        reject_reason=reject,
    )
    still_active = status not in _TERMINAL_STATUSES
    return (text, order, still_active, 0)


async def run_bot_order_watch_tick() -> None:
    async with session_scope() as session:
        watches = await fetch_active_bot_order_watches(session)
        if not watches:
            return

    now = datetime.now(tz=MSK)
    for watch in watches:
        try:
            text, order, still_active, miss_count = await asyncio.to_thread(
                _check_watch_sync, watch
            )
        except Exception:
            log.exception("bot order watch id=%s", watch.id)
            continue

        new_status = watch.order_status
        new_cum = watch.cum_exec_qty or "0"
        new_avg = watch.avg_price or ""
        if order is not None:
            new_status = _order_field(order, "orderStatus", watch.order_status)
            new_cum = _order_field(order, "cumExecQty", "0")
            new_avg = _order_field(order, "avgPrice")

        async with session_scope() as session:
            await update_bot_order_watch_state(
                session,
                watch.id,
                order_status=new_status,
                cum_exec_qty=new_cum,
                avg_price=new_avg,
                active=still_active,
                miss_count=miss_count,
                checked_at=now,
            )
            await session.commit()

        if text:
            try:
                await reply_to_chat_message(
                    watch.telegram_chat_id,
                    watch.telegram_message_id,
                    text,
                )
                log.info(
                    "bot order watch notify %s %s %s → %s",
                    watch.symbol,
                    watch.bybit_order_id[:8],
                    watch.order_status,
                    new_status,
                )
            except Exception:
                log.exception(
                    "bot order watch telegram %s id=%s", watch.symbol, watch.id
                )
