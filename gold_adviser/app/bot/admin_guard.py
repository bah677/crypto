"""Проверка admins с коротким TTL-кэшем."""

from __future__ import annotations

import time

from app.config import get_settings
from app.db.session import session_scope
from app.repository.admins import is_telegram_admin

_CACHE: dict[int, tuple[bool, float]] = {}
_TTL_SEC = 45.0


async def is_admin_user(telegram_user_id: int) -> bool:
    s = get_settings()
    if telegram_user_id == s.superadmin_telegram_id:
        return True
    now = time.monotonic()
    hit = _CACHE.get(telegram_user_id)
    if hit is not None:
        ok, ts = hit
        if now - ts < _TTL_SEC:
            return ok
    async with session_scope() as session:
        ok = await is_telegram_admin(session, telegram_user_id)
    _CACHE[telegram_user_id] = (ok, now)
    return ok


def invalidate_admin_cache(telegram_user_id: int) -> None:
    _CACHE.pop(telegram_user_id, None)


def clear_admin_cache() -> None:
    _CACHE.clear()
