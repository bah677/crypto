"""Вкл/выкл автоалертов: .env разрешает, БД — переключатель из меню."""

from __future__ import annotations

from app.config import Settings, get_settings
from app.db.models import BotAlertsFlags
from app.db.session import session_scope
from app.repository.alerts_flags import get_alerts_flags


async def load_alerts_flags() -> BotAlertsFlags:
    async with session_scope() as session:
        return await get_alerts_flags(session)


def ema_sl_env_allowed(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return s.is_advisor_mode and s.ema_sl_monitor_enabled


def price_spike_env_allowed(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return s.is_advisor_mode and s.price_spike_monitor_enabled


async def ema_sl_reports_active() -> bool:
    s = get_settings()
    if not ema_sl_env_allowed(s):
        return False
    flags = await load_alerts_flags()
    return flags.ema_sl_reports


async def price_spike_reports_active() -> bool:
    s = get_settings()
    if not price_spike_env_allowed(s):
        return False
    flags = await load_alerts_flags()
    return flags.price_spike_reports


def funding_env_allowed(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return s.funding_scan_enabled


async def funding_reports_active() -> bool:
    s = get_settings()
    if not funding_env_allowed(s):
        return False
    flags = await load_alerts_flags()
    return flags.funding_reports
