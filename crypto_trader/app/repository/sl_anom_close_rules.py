from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SlAnomCloseRuleRow


async def fetch_enabled_sl_anom_close_rules(session: AsyncSession) -> list[SlAnomCloseRuleRow]:
    r = await session.execute(
        select(SlAnomCloseRuleRow).where(SlAnomCloseRuleRow.enabled.is_(True))
    )
    return list(r.scalars().all())


async def fetch_all_sl_anom_close_rules(session: AsyncSession) -> list[SlAnomCloseRuleRow]:
    r = await session.execute(select(SlAnomCloseRuleRow).order_by(SlAnomCloseRuleRow.symbol))
    return list(r.scalars().all())


async def get_sl_anom_close_rule_by_symbol(
    session: AsyncSession, symbol: str
) -> SlAnomCloseRuleRow | None:
    r = await session.execute(
        select(SlAnomCloseRuleRow).where(SlAnomCloseRuleRow.symbol == symbol.upper())
    )
    return r.scalar_one_or_none()


async def upsert_sl_anom_close_rule(
    session: AsyncSession,
    *,
    symbol: str,
    position_side: str,
) -> SlAnomCloseRuleRow:
    sym = symbol.upper()
    row = await get_sl_anom_close_rule_by_symbol(session, sym)
    if row is None:
        row = SlAnomCloseRuleRow(
            symbol=sym,
            position_side=position_side,
            enabled=True,
            last_processed_bar_open_ms=None,
            pending_anomaly_bar_open_ms=None,
            pending_anomaly_body=None,
        )
        session.add(row)
    else:
        row.position_side = position_side
        row.enabled = True
        row.last_processed_bar_open_ms = None
        row.pending_anomaly_bar_open_ms = None
        row.pending_anomaly_body = None
    await session.commit()
    await session.refresh(row)
    return row


async def disable_sl_anom_close_rule(session: AsyncSession, symbol: str) -> bool:
    row = await get_sl_anom_close_rule_by_symbol(session, symbol.upper())
    if row is None:
        return False
    row.enabled = False
    await session.commit()
    return True


async def update_sl_anom_close_cursor(
    session: AsyncSession,
    row_id: int,
    *,
    last_processed_bar_open_ms: int,
    pending_anomaly_bar_open_ms: int | None,
    pending_anomaly_body: float | None,
) -> None:
    r = await session.execute(select(SlAnomCloseRuleRow).where(SlAnomCloseRuleRow.id == row_id))
    row = r.scalar_one_or_none()
    if row is None:
        return
    row.last_processed_bar_open_ms = last_processed_bar_open_ms
    row.pending_anomaly_bar_open_ms = pending_anomaly_bar_open_ms
    row.pending_anomaly_body = pending_anomaly_body
    await session.commit()

