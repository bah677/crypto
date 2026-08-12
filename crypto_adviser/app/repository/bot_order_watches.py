from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BotOrderWatchRow


async def create_bot_order_watch(
    session: AsyncSession,
    *,
    telegram_chat_id: int,
    telegram_message_id: int,
    telegram_user_id: int,
    bybit_order_id: str,
    symbol: str,
    side: str,
    order_type: str,
    qty: str,
    price: str,
    order_status: str,
    cum_exec_qty: str = "0",
    avg_price: str = "",
    source: str = "pump",
) -> BotOrderWatchRow:
    row = BotOrderWatchRow(
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
        telegram_user_id=telegram_user_id,
        bybit_order_id=bybit_order_id,
        symbol=symbol.upper(),
        side=side,
        order_type=order_type,
        qty=qty,
        price=price,
        order_status=order_status,
        cum_exec_qty=cum_exec_qty or "0",
        avg_price=avg_price or "",
        source=source,
        active=True,
    )
    session.add(row)
    await session.flush()
    return row


async def fetch_active_bot_order_watches(session: AsyncSession) -> list[BotOrderWatchRow]:
    r = await session.execute(
        select(BotOrderWatchRow)
        .where(BotOrderWatchRow.active.is_(True))
        .order_by(BotOrderWatchRow.id)
    )
    return list(r.scalars().all())


async def update_bot_order_watch_state(
    session: AsyncSession,
    watch_id: int,
    *,
    order_status: str,
    cum_exec_qty: str,
    avg_price: str,
    active: bool,
    miss_count: int,
    checked_at: datetime,
) -> None:
    await session.execute(
        update(BotOrderWatchRow)
        .where(BotOrderWatchRow.id == watch_id)
        .values(
            order_status=order_status,
            cum_exec_qty=cum_exec_qty,
            avg_price=avg_price,
            active=active,
            miss_count=miss_count,
            last_checked_at=checked_at,
        )
    )
