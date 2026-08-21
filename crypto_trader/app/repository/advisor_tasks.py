from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdvisorTaskRow


async def fetch_enabled_advisor_tasks(session: AsyncSession) -> list[AdvisorTaskRow]:
    res = await session.execute(
        select(AdvisorTaskRow)
        .where(AdvisorTaskRow.enabled.is_(True))
        .order_by(AdvisorTaskRow.id)
    )
    return list(res.scalars().all())


async def fetch_all_advisor_tasks(session: AsyncSession) -> list[AdvisorTaskRow]:
    res = await session.execute(select(AdvisorTaskRow).order_by(AdvisorTaskRow.id))
    return list(res.scalars().all())


async def count_advisor_tasks(session: AsyncSession) -> int:
    res = await session.execute(select(func.count()).select_from(AdvisorTaskRow))
    return int(res.scalar_one())


async def get_advisor_task(session: AsyncSession, task_id: int) -> AdvisorTaskRow | None:
    res = await session.execute(
        select(AdvisorTaskRow).where(AdvisorTaskRow.id == task_id)
    )
    return res.scalar_one_or_none()


async def find_advisor_task_by_key(
    session: AsyncSession,
    *,
    symbol: str,
    kline_interval: str,
    ema_fast: int,
    ema_slow: int,
    bybit_category: str,
    exclude_id: int | None = None,
) -> AdvisorTaskRow | None:
    q = select(AdvisorTaskRow).where(
        AdvisorTaskRow.symbol == symbol.upper(),
        AdvisorTaskRow.kline_interval == kline_interval,
        AdvisorTaskRow.ema_fast == ema_fast,
        AdvisorTaskRow.ema_slow == ema_slow,
        AdvisorTaskRow.bybit_category == bybit_category,
    )
    if exclude_id is not None:
        q = q.where(AdvisorTaskRow.id != exclude_id)
    res = await session.execute(q)
    return res.scalar_one_or_none()


async def set_advisor_task_enabled(
    session: AsyncSession, task_id: int, enabled: bool
) -> None:
    await session.execute(
        update(AdvisorTaskRow)
        .where(AdvisorTaskRow.id == task_id)
        .values(enabled=enabled)
    )
    await session.commit()


async def update_last_evaluated_bar_open(
    session: AsyncSession, task_id: int, open_ms: int
) -> None:
    await session.execute(
        update(AdvisorTaskRow)
        .where(AdvisorTaskRow.id == task_id)
        .values(last_evaluated_bar_open_ms=open_ms)
    )
    await session.commit()


async def add_advisor_task(
    session: AsyncSession,
    *,
    symbol: str,
    ema_fast: int,
    ema_slow: int,
    kline_interval: str,
    bybit_category: str,
    trading_hours: list[dict[str, str]],
    alias: str = "",
    enabled: bool = False,
    last_evaluated_bar_open_ms: int | None = None,
) -> AdvisorTaskRow:
    row = AdvisorTaskRow(
        symbol=symbol.strip().upper(),
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        kline_interval=kline_interval,
        bybit_category=bybit_category,
        alias=alias.strip()[:64],
        enabled=enabled,
        last_evaluated_bar_open_ms=last_evaluated_bar_open_ms,
    )
    row.set_trading_hours(trading_hours)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_advisor_task(
    session: AsyncSession,
    task_id: int,
    *,
    symbol: str,
    ema_fast: int,
    ema_slow: int,
    kline_interval: str,
    bybit_category: str,
    trading_hours: list[dict[str, str]],
    alias: str,
) -> AdvisorTaskRow | None:
    row = await get_advisor_task(session, task_id)
    if row is None:
        return None
    row.symbol = symbol.strip().upper()
    row.ema_fast = ema_fast
    row.ema_slow = ema_slow
    row.kline_interval = kline_interval
    row.bybit_category = bybit_category
    row.alias = alias.strip()[:64]
    row.set_trading_hours(trading_hours)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_advisor_task(session: AsyncSession, task_id: int) -> bool:
    res = await session.execute(
        delete(AdvisorTaskRow).where(AdvisorTaskRow.id == task_id)
    )
    await session.commit()
    return res.rowcount > 0
