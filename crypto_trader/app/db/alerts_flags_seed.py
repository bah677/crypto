"""Строка bot_alerts_flags (id=1) при первом старте."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import BotAlertsFlags


async def seed_alerts_flags_if_empty(session: AsyncSession) -> None:
    r = await session.execute(select(BotAlertsFlags).where(BotAlertsFlags.id == 1))
    if r.scalar_one_or_none() is not None:
        return
    s = get_settings()
    session.add(
        BotAlertsFlags(
            id=1,
            ema_sl_reports=s.ema_sl_monitor_enabled and s.is_advisor_mode,
            price_spike_reports=s.price_spike_monitor_enabled and s.is_advisor_mode,
            funding_reports=s.funding_scan_enabled,
        )
    )
    await session.commit()
