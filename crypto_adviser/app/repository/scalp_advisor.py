from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScalpAdvisorTaskRow
from app.scalp_advisor.strategy_params import ScalpStrategyParams, default_scalp_strategy
from app.scalp_advisor.tasks import TRADE_IDLE, TRADE_OPEN


async def fetch_enabled_scalp_tasks(session: AsyncSession) -> list[ScalpAdvisorTaskRow]:
    res = await session.execute(
        select(ScalpAdvisorTaskRow)
        .where(ScalpAdvisorTaskRow.enabled.is_(True))
        .order_by(ScalpAdvisorTaskRow.id)
    )
    return list(res.scalars().all())


async def fetch_all_scalp_tasks(session: AsyncSession) -> list[ScalpAdvisorTaskRow]:
    res = await session.execute(select(ScalpAdvisorTaskRow).order_by(ScalpAdvisorTaskRow.id))
    return list(res.scalars().all())


async def get_scalp_task(session: AsyncSession, task_id: int) -> ScalpAdvisorTaskRow | None:
    res = await session.execute(
        select(ScalpAdvisorTaskRow).where(ScalpAdvisorTaskRow.id == task_id)
    )
    return res.scalar_one_or_none()


async def find_scalp_task_by_symbol(
    session: AsyncSession, symbol: str, *, exclude_id: int | None = None
) -> ScalpAdvisorTaskRow | None:
    q = select(ScalpAdvisorTaskRow).where(ScalpAdvisorTaskRow.symbol == symbol.upper())
    if exclude_id is not None:
        q = q.where(ScalpAdvisorTaskRow.id != exclude_id)
    res = await session.execute(q)
    return res.scalar_one_or_none()


async def set_scalp_task_enabled(session: AsyncSession, task_id: int, enabled: bool) -> None:
    await session.execute(
        update(ScalpAdvisorTaskRow).where(ScalpAdvisorTaskRow.id == task_id).values(enabled=enabled)
    )
    await session.commit()


async def update_scalp_last_m1_bar(session: AsyncSession, task_id: int, open_ms: int) -> None:
    await session.execute(
        update(ScalpAdvisorTaskRow)
        .where(ScalpAdvisorTaskRow.id == task_id)
        .values(last_evaluated_m1_bar_ms=open_ms)
    )
    await session.commit()


async def open_scalp_trade(
    session: AsyncSession,
    task_id: int,
    *,
    side: str,
    entry: float,
    entry_ms: int,
    sl: float,
    tp1: float,
    tp2: float,
    m1_bar_ms: int,
) -> None:
    await session.execute(
        update(ScalpAdvisorTaskRow)
        .where(ScalpAdvisorTaskRow.id == task_id)
        .values(
            trade_state=TRADE_OPEN,
            trade_side=side,
            entry_price=entry,
            entry_ms=entry_ms,
            trade_sl=sl,
            initial_sl=sl,
            trade_tp1=tp1,
            trade_tp2=tp2,
            tp1_hit=False,
            tp2_hit=False,
            last_reported_sl=sl,
            last_m5_sl_bar_ms=None,
            last_evaluated_m1_bar_ms=m1_bar_ms,
        )
    )
    await session.commit()


async def update_scalp_trade(
    session: AsyncSession,
    task_id: int,
    *,
    sl: float | None = None,
    tp1_hit: bool | None = None,
    tp2_hit: bool | None = None,
    last_reported_sl: float | None = None,
    last_m5_sl_bar_ms: int | None = None,
) -> None:
    values: dict = {}
    if sl is not None:
        values["trade_sl"] = sl
    if tp1_hit is not None:
        values["tp1_hit"] = tp1_hit
    if tp2_hit is not None:
        values["tp2_hit"] = tp2_hit
    if last_reported_sl is not None:
        values["last_reported_sl"] = last_reported_sl
    if last_m5_sl_bar_ms is not None:
        values["last_m5_sl_bar_ms"] = last_m5_sl_bar_ms
    if values:
        await session.execute(
            update(ScalpAdvisorTaskRow).where(ScalpAdvisorTaskRow.id == task_id).values(**values)
        )
        await session.commit()


async def close_scalp_trade(session: AsyncSession, task_id: int) -> None:
    await session.execute(
        update(ScalpAdvisorTaskRow)
        .where(ScalpAdvisorTaskRow.id == task_id)
        .values(
            trade_state=TRADE_IDLE,
            trade_side=None,
            entry_price=None,
            entry_ms=None,
            trade_sl=None,
            initial_sl=None,
            trade_tp1=None,
            trade_tp2=None,
            tp1_hit=False,
            tp2_hit=False,
            last_reported_sl=None,
            last_m5_sl_bar_ms=None,
        )
    )
    await session.commit()


async def add_scalp_task(
    session: AsyncSession,
    *,
    symbol: str,
    levels: list[float],
    trading_hours: list[dict[str, str]],
    alias: str = "",
    enabled: bool = False,
) -> ScalpAdvisorTaskRow:
    row = ScalpAdvisorTaskRow(
        symbol=symbol.strip().upper(),
        alias=alias.strip()[:64],
        enabled=enabled,
        trail_hint=True,
    )
    row.set_levels(levels)
    row.set_trading_hours(trading_hours)
    row.set_strategy_params(default_scalp_strategy())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_scalp_task(session: AsyncSession, task_id: int) -> bool:
    res = await session.execute(
        delete(ScalpAdvisorTaskRow).where(ScalpAdvisorTaskRow.id == task_id)
    )
    await session.commit()
    return res.rowcount > 0


async def update_scalp_strategy(
    session: AsyncSession,
    task_id: int,
    params: ScalpStrategyParams,
) -> ScalpAdvisorTaskRow | None:
    row = await get_scalp_task(session, task_id)
    if not row:
        return None
    row.set_strategy_params(params)
    await session.commit()
    await session.refresh(row)
    return row


async def update_scalp_levels(
    session: AsyncSession,
    task_id: int,
    levels: list[float],
) -> ScalpAdvisorTaskRow | None:
    if len(levels) < 2:
        raise ValueError("Нужно минимум 2 уровня")
    row = await get_scalp_task(session, task_id)
    if not row:
        return None
    row.set_levels(sorted(set(levels)))
    await session.commit()
    await session.refresh(row)
    return row
