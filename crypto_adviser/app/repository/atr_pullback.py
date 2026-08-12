from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AtrPullbackTaskRow


async def fetch_enabled_atr_pullback_tasks(
    session: AsyncSession,
) -> list[AtrPullbackTaskRow]:
    res = await session.execute(
        select(AtrPullbackTaskRow)
        .where(AtrPullbackTaskRow.enabled.is_(True))
        .order_by(AtrPullbackTaskRow.id)
    )
    return list(res.scalars().all())


async def fetch_all_atr_pullback_tasks(
    session: AsyncSession,
) -> list[AtrPullbackTaskRow]:
    res = await session.execute(select(AtrPullbackTaskRow).order_by(AtrPullbackTaskRow.id))
    return list(res.scalars().all())


async def get_atr_pullback_task(
    session: AsyncSession, task_id: int
) -> AtrPullbackTaskRow | None:
    res = await session.execute(
        select(AtrPullbackTaskRow).where(AtrPullbackTaskRow.id == task_id)
    )
    return res.scalar_one_or_none()


async def find_atr_pullback_task_by_key(
    session: AsyncSession,
    *,
    symbol: str,
    btf_interval: str,
    mtf_interval: str,
    ema_fast: int,
    ema_slow: int,
    exclude_id: int | None = None,
) -> AtrPullbackTaskRow | None:
    q = select(AtrPullbackTaskRow).where(
        AtrPullbackTaskRow.symbol == symbol.upper(),
        AtrPullbackTaskRow.btf_interval == btf_interval,
        AtrPullbackTaskRow.mtf_interval == mtf_interval,
        AtrPullbackTaskRow.ema_fast == ema_fast,
        AtrPullbackTaskRow.ema_slow == ema_slow,
    )
    if exclude_id is not None:
        q = q.where(AtrPullbackTaskRow.id != exclude_id)
    res = await session.execute(q)
    return res.scalar_one_or_none()


async def set_atr_pullback_enabled(
    session: AsyncSession, task_id: int, enabled: bool
) -> None:
    await session.execute(
        update(AtrPullbackTaskRow)
        .where(AtrPullbackTaskRow.id == task_id)
        .values(enabled=enabled)
    )
    await session.commit()


async def add_atr_pullback_task(
    session: AsyncSession,
    *,
    symbol: str,
    ema_fast: int,
    ema_slow: int,
    btf_interval: str,
    mtf_interval: str,
    trading_hours: list[dict[str, str]],
    alias: str = "",
    auto_trade: bool = False,
    position_usd: float = 0.0,
    leverage: int = 1,
    enabled: bool = False,
) -> AtrPullbackTaskRow:
    row = AtrPullbackTaskRow(
        symbol=symbol.strip().upper(),
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        btf_interval=btf_interval,
        mtf_interval=mtf_interval,
        alias=alias.strip()[:64],
        enabled=enabled,
        auto_trade=auto_trade,
        position_usd=position_usd,
        leverage=leverage,
        state="idle",
    )
    row.set_trading_hours(trading_hours)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_atr_pullback_task(session: AsyncSession, task_id: int) -> bool:
    res = await session.execute(
        delete(AtrPullbackTaskRow).where(AtrPullbackTaskRow.id == task_id)
    )
    await session.commit()
    return res.rowcount > 0


async def update_atr_pullback_state(
    session: AsyncSession,
    task_id: int,
    *,
    state: str | None = None,
    armed_side: str | None = None,
    armed_at_ms: int | None = None,
    btf_cross_bar_open_ms: int | None = None,
    cross_price: float | None = None,
    last_evaluated_btf_bar_ms: int | None = None,
    last_evaluated_mtf_bar_ms: int | None = None,
    last_sl_update_ms: int | None = None,
    clear_armed: bool = False,
) -> None:
    values: dict = {}
    if state is not None:
        values["state"] = state
    if clear_armed:
        values.update(
            {
                "armed_side": None,
                "armed_at_ms": None,
                "btf_cross_bar_open_ms": None,
                "cross_price": None,
            }
        )
    else:
        if armed_side is not None:
            values["armed_side"] = armed_side
        if armed_at_ms is not None:
            values["armed_at_ms"] = armed_at_ms
        if btf_cross_bar_open_ms is not None:
            values["btf_cross_bar_open_ms"] = btf_cross_bar_open_ms
        if cross_price is not None:
            values["cross_price"] = cross_price
    if last_evaluated_btf_bar_ms is not None:
        values["last_evaluated_btf_bar_ms"] = last_evaluated_btf_bar_ms
    if last_evaluated_mtf_bar_ms is not None:
        values["last_evaluated_mtf_bar_ms"] = last_evaluated_mtf_bar_ms
    if last_sl_update_ms is not None:
        values["last_sl_update_ms"] = last_sl_update_ms
    if not values:
        return
    await session.execute(
        update(AtrPullbackTaskRow).where(AtrPullbackTaskRow.id == task_id).values(**values)
    )
    await session.commit()
