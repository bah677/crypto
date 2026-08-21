from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PumpEmaAlarmRow


async def create_pump_ema_alarm(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    symbol: str,
    ema_key: str,
    direction: str,
    last_side: str | None,
    last_ema_value: float | None,
) -> PumpEmaAlarmRow:
    row = PumpEmaAlarmRow(
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        symbol=symbol.upper(),
        ema_key=ema_key.upper(),
        direction=direction,
        active=True,
        last_side=last_side,
        last_ema_value=last_ema_value,
    )
    session.add(row)
    await session.flush()
    return row


async def fetch_active_pump_ema_alarms(session: AsyncSession) -> list[PumpEmaAlarmRow]:
    r = await session.execute(
        select(PumpEmaAlarmRow)
        .where(PumpEmaAlarmRow.active.is_(True))
        .order_by(PumpEmaAlarmRow.id)
    )
    return list(r.scalars().all())


async def fetch_user_pump_ema_alarms(
    session: AsyncSession, telegram_user_id: int, *, active_only: bool = False
) -> list[PumpEmaAlarmRow]:
    q = select(PumpEmaAlarmRow).where(PumpEmaAlarmRow.telegram_user_id == telegram_user_id)
    if active_only:
        q = q.where(PumpEmaAlarmRow.active.is_(True))
    q = q.order_by(PumpEmaAlarmRow.id.desc())
    r = await session.execute(q)
    return list(r.scalars().all())


async def set_pump_ema_alarm_active(
    session: AsyncSession, alarm_id: int, *, active: bool, user_id: int | None = None
) -> bool:
    q = update(PumpEmaAlarmRow).where(PumpEmaAlarmRow.id == alarm_id)
    if user_id is not None:
        q = q.where(PumpEmaAlarmRow.telegram_user_id == user_id)
    r = await session.execute(q.values(active=active))
    return (r.rowcount or 0) > 0


async def update_pump_ema_alarm_state(
    session: AsyncSession,
    alarm_id: int,
    *,
    last_side: str,
    last_ema_value: float,
    checked_at: datetime,
    triggered: bool = False,
) -> None:
    values: dict = {
        "last_side": last_side,
        "last_ema_value": last_ema_value,
        "last_checked_at": checked_at,
    }
    if triggered:
        row = await session.get(PumpEmaAlarmRow, alarm_id)
        if row:
            values["trigger_count"] = int(row.trigger_count or 0) + 1
            values["last_triggered_at"] = checked_at
    await session.execute(
        update(PumpEmaAlarmRow).where(PumpEmaAlarmRow.id == alarm_id).values(**values)
    )
