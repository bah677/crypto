from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AdminRow
from app.repository.admins import add_admin, is_telegram_admin


async def seed_admins_if_empty(session: AsyncSession) -> None:
    """Первый запуск: добавить SUPERADMIN_TELEGRAM_ID в admins."""
    r = await session.execute(select(func.count()).select_from(AdminRow))
    if (r.scalar_one() or 0) > 0:
        return
    s = get_settings()
    await add_admin(
        session,
        s.superadmin_telegram_id,
        note="bootstrap from SUPERADMIN_TELEGRAM_ID",
    )


async def ensure_superadmin_in_admins(session: AsyncSession) -> None:
    """SUPERADMIN_TELEGRAM_ID из .env всегда остаётся в таблице admins."""
    s = get_settings()
    if not await is_telegram_admin(session, s.superadmin_telegram_id):
        await add_admin(
            session,
            s.superadmin_telegram_id,
            note="superadmin from env",
        )
