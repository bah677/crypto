"""Сид: супер-админ автоматически в подписчиках."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import SubscriberRow


async def ensure_superadmin_subscribed(session: AsyncSession) -> None:
    s = get_settings()
    uid = int(s.superadmin_telegram_id)
    row = await session.get(SubscriberRow, uid)
    now = datetime.now(timezone.utc)
    if row is None:
        session.add(
            SubscriberRow(
                telegram_user_id=uid,
                telegram_chat_id=uid,
                subscribed=True,
                banned=False,
                subscribed_at=now,
                last_seen_at=now,
            )
        )
    else:
        row.subscribed = True
        row.banned = False
        if row.subscribed_at is None:
            row.subscribed_at = now
    await session.commit()
