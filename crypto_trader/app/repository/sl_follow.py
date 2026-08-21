from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SlFollowRow


async def fetch_enabled_sl_follow(session: AsyncSession) -> list[SlFollowRow]:
    r = await session.execute(
        select(SlFollowRow).where(
            SlFollowRow.enabled.is_(True),
        )
    )
    return list(r.scalars().all())


async def fetch_all_sl_follow(session: AsyncSession) -> list[SlFollowRow]:
    r = await session.execute(select(SlFollowRow).order_by(SlFollowRow.symbol))
    return list(r.scalars().all())


async def get_sl_follow_by_symbol(
    session: AsyncSession, symbol: str
) -> SlFollowRow | None:
    r = await session.execute(
        select(SlFollowRow).where(SlFollowRow.symbol == symbol.upper())
    )
    return r.scalar_one_or_none()


async def upsert_sl_follow(
    session: AsyncSession,
    *,
    symbol: str,
    position_side: str,
    advisor_task_id: int,
    sl_tf_mode: str,
    allow_sl_widen: bool,
) -> SlFollowRow:
    sym = symbol.upper()
    row = await get_sl_follow_by_symbol(session, sym)
    if row is None:
        row = SlFollowRow(
            symbol=sym,
            position_side=position_side,
            advisor_task_id=advisor_task_id,
            sl_tf_mode=sl_tf_mode,
            allow_sl_widen=allow_sl_widen,
            enabled=True,
            last_processed_bar_open_ms=None,
        )
        session.add(row)
    else:
        row.position_side = position_side
        row.advisor_task_id = advisor_task_id
        row.sl_tf_mode = sl_tf_mode
        row.allow_sl_widen = allow_sl_widen
        row.enabled = True
        row.last_processed_bar_open_ms = None
    await session.commit()
    await session.refresh(row)
    return row


async def disable_sl_follow(session: AsyncSession, symbol: str) -> bool:
    row = await get_sl_follow_by_symbol(session, symbol.upper())
    if row is None:
        return False
    row.enabled = False
    await session.commit()
    return True


async def delete_sl_follow(session: AsyncSession, symbol: str) -> bool:
    row = await get_sl_follow_by_symbol(session, symbol.upper())
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def update_sl_follow_bar_cursor(
    session: AsyncSession, row_id: int, bar_open_ms: int
) -> None:
    r = await session.execute(select(SlFollowRow).where(SlFollowRow.id == row_id))
    row = r.scalar_one_or_none()
    if row is None:
        return
    row.last_processed_bar_open_ms = bar_open_ms
    await session.commit()
