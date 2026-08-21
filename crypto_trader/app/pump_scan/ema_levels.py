"""Общие EMA-уровни 1D/1W для мастера ордеров и будильников."""

from __future__ import annotations

from app.bybit.rest import BybitRest
from app.pump_scan.daily_ema import compute_daily_emas
from app.pump_scan.weekly_ema import compute_weekly_emas

EMA_KEYS_1D = ("50", "100", "200")
EMA_KEYS_1W = ("7W", "14W", "28W")
ALL_EMA_KEYS = EMA_KEYS_1D + EMA_KEYS_1W


def build_ema_map(
    daily: object | None,
    weekly: object | None,
) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    if daily is not None:
        out.update(
            {
                "50": getattr(daily, "ema50", None),
                "100": getattr(daily, "ema100", None),
                "200": getattr(daily, "ema200", None),
            }
        )
    if weekly is not None:
        out.update(weekly.as_label_map())  # type: ignore[union-attr]
    return out


def fetch_ema_map_sync(client: BybitRest, symbol: str) -> dict[str, float | None]:
    daily = compute_daily_emas(client, symbol)
    weekly = compute_weekly_emas(client, symbol)
    return build_ema_map(daily, weekly)


def ema_price_from_map(ema_map: dict[str, float | None], ema_key: str) -> float | None:
    key = (ema_key or "").strip().upper()
    val = ema_map.get(key)
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def price_side(price: float, ema: float) -> str:
    return "above" if price > ema else "below"
