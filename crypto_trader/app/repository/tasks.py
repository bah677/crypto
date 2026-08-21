from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import TaskLevel, TradingTask


async def fetch_enabled_tasks(session: AsyncSession) -> list[TradingTask]:
    res = await session.execute(
        select(TradingTask)
        .where(
            TradingTask.enabled.is_(True),
            TradingTask.trading_channel == "bybit_v5",
        )
        .options(selectinload(TradingTask.levels))
        .order_by(TradingTask.id)
    )
    return list(res.scalars().unique().all())


async def fetch_enabled_mt5_tasks(session: AsyncSession) -> list[TradingTask]:
    res = await session.execute(
        select(TradingTask)
        .where(
            TradingTask.enabled.is_(True),
            TradingTask.trading_channel == "mt5",
        )
        .options(selectinload(TradingTask.levels))
        .order_by(TradingTask.id)
    )
    return list(res.scalars().unique().all())


async def fetch_all_tasks(session: AsyncSession) -> list[TradingTask]:
    res = await session.execute(
        select(TradingTask)
        .options(selectinload(TradingTask.levels))
        .order_by(TradingTask.id)
    )
    return list(res.scalars().unique().all())


async def get_task(session: AsyncSession, task_id: int) -> TradingTask | None:
    res = await session.execute(
        select(TradingTask)
        .where(TradingTask.id == task_id)
        .options(selectinload(TradingTask.levels))
    )
    return res.scalar_one_or_none()


async def set_task_enabled(session: AsyncSession, task_id: int, enabled: bool) -> None:
    await session.execute(
        update(TradingTask).where(TradingTask.id == task_id).values(enabled=enabled)
    )
    await session.commit()


async def update_last_evaluated_bar_open(
    session: AsyncSession, task_id: int, open_ms: int
) -> None:
    await session.execute(
        update(TradingTask)
        .where(TradingTask.id == task_id)
        .values(last_evaluated_bar_open_ms=open_ms)
    )
    await session.commit()


async def add_task(
    session: AsyncSession,
    *,
    symbol: str,
    trading_channel: str,
    ema_fast: int,
    ema_slow: int,
    kline_interval: str,
    delta_ticks: int,
    take_profit_ticks: int,
    stop_loss_ticks: int,
    order_qty: str,
    trading_hours: list[dict[str, str]],
    levels: list[str],
) -> TradingTask:
    if trading_channel not in ("bybit_v5", "mt5"):
        raise ValueError("trading_channel должен быть bybit_v5 или mt5")
    task = TradingTask(
        symbol=symbol.strip(),
        trading_channel=trading_channel,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        kline_interval=kline_interval,
        delta_ticks=delta_ticks,
        take_profit_ticks=take_profit_ticks,
        stop_loss_ticks=stop_loss_ticks,
        order_qty=order_qty,
        enabled=False,
    )
    task.set_trading_hours(trading_hours)
    session.add(task)
    await session.flush()
    for p in levels:
        session.add(TaskLevel(task_id=task.id, price=p.strip()))
    await session.commit()
    await session.refresh(task)
    return task
