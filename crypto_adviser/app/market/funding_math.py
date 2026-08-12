"""Пересчёт funding Bybit в годовые проценты (как в UI биржи)."""

from __future__ import annotations

HOURS_PER_YEAR = 24 * 365  # 8760


def funding_rate_interval_percent(funding_rate_raw: str | float) -> float:
    """Ставка за один funding-интервал, в % (API: 0.0001 → 0.01%)."""
    return float(funding_rate_raw) * 100.0


def funding_rate_annual_percent(
    funding_rate_raw: str | float,
    interval_hours: float,
) -> float:
    """
    Годовая ставка funding, %.

    Bybit UI: annual = rate_per_interval_pct × (8760 / interval_hours).
    Пример: -2.5% / 1ч → -21900% годовых.
    """
    if interval_hours <= 0:
        raise ValueError("interval_hours must be positive")
    interval_pct = funding_rate_interval_percent(funding_rate_raw)
    return interval_pct * (HOURS_PER_YEAR / interval_hours)


def funding_direction_ru(annual_pct: float) -> str:
    """Кто кому платит (как в UI Bybit)."""
    if annual_pct > 0:
        return "Лонг платит Шорт"
    if annual_pct < 0:
        return "Шорт платит Лонг"
    return "—"
