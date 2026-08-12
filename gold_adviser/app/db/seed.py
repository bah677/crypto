from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AdminRow, GoldSettingsRow
from app.repository.admins import add_admin, is_telegram_admin


async def seed_admins_if_empty(session: AsyncSession) -> None:
    r = await session.execute(select(func.count()).select_from(AdminRow))
    if (r.scalar_one() or 0) > 0:
        return
    s = get_settings()
    await add_admin(
        session,
        s.superadmin_telegram_id,
        note="bootstrap from SUPERADMIN_TELEGRAM_ID",
        commit=False,
    )


async def ensure_superadmin_in_admins(session: AsyncSession) -> None:
    s = get_settings()
    if not await is_telegram_admin(session, s.superadmin_telegram_id):
        await add_admin(
            session,
            s.superadmin_telegram_id,
            note="superadmin from env",
            commit=False,
        )


async def seed_settings_if_empty(session: AsyncSession) -> None:
    row = await session.get(GoldSettingsRow, 1)
    if row is not None:
        return
    s = get_settings()
    session.add(
        GoldSettingsRow(
            id=1,
            enabled=bool(s.default_enabled),
            body_mult=float(s.default_body_mult),
            lookback=int(s.default_lookback),
            settings_cache_ttl_sec=int(s.default_settings_cache_ttl_sec),
        )
    )
