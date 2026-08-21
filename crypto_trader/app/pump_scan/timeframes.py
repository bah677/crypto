"""Профили детекции по таймфреймам Bybit."""

from __future__ import annotations

from dataclasses import dataclass

# Bybit: 1,3,5,15,30,60,120,240,360,720,D,W,M
INTERVAL_LABELS: dict[str, str] = {
    "1": "1m",
    "3": "3m",
    "5": "5m",
    "15": "15m",
    "30": "30m",
    "60": "1h",
    "120": "2h",
    "240": "4h",
    "360": "6h",
    "720": "12h",
    "D": "1D",
    "W": "1W",
    "M": "1M",
}

DEFAULT_FAST_INTERVALS = ("5", "15", "30", "60")
DEFAULT_SLOW_INTERVALS = ("240", "D")


@dataclass(frozen=True)
class TfProfile:
    interval: str
    spike_pct: float
    smooth_pct: float
    smooth_bars: int
    rvol_threshold: float
    smooth_rvol: float
    lookback: int
    min_green_red_ratio: float
    smooth_min_green_ratio: float
    kline_limit: int = 120


# Пороги масштабируются с длиной свечи (4h/1D — меньше шума, больше %).
_TF_DEFAULTS: dict[str, TfProfile] = {
    "5": TfProfile("5", 4.5, 9.0, 6, 3.0, 2.0, 20, 2.0, 1.2, 80),
    "15": TfProfile("15", 5.5, 10.0, 4, 2.8, 1.9, 20, 1.8, 1.15, 80),
    "30": TfProfile("30", 6.5, 12.0, 4, 2.5, 1.8, 18, 1.6, 1.1, 70),
    "60": TfProfile("60", 7.0, 14.0, 3, 2.3, 1.7, 16, 1.5, 1.1, 70),
    "240": TfProfile("240", 8.0, 15.0, 3, 1.5, 1.4, 14, 1.2, 1.0, 60),
    "D": TfProfile("D", 12.0, 20.0, 2, 1.3, 1.2, 12, 1.1, 1.0, 50),
}


def parse_interval_list(raw: str, *, fallback: tuple[str, ...]) -> list[str]:
    if not raw or not raw.strip():
        return list(fallback)
    out: list[str] = []
    for part in raw.replace(";", ",").split(","):
        iv = part.strip().upper()
        if iv == "4H":
            iv = "240"
        elif iv in ("1D", "DAY"):
            iv = "D"
        if iv and iv not in out:
            out.append(iv)
    return out or list(fallback)


def profile_for(interval: str) -> TfProfile | None:
    return _TF_DEFAULTS.get(interval.upper())


def interval_label(interval: str) -> str:
    return INTERVAL_LABELS.get(interval.upper(), interval)


def interval_minutes(interval: str) -> int:
    iv = interval.upper()
    if iv == "D":
        return 1440
    if iv == "W":
        return 10080
    if iv == "M":
        return 43200
    try:
        return int(iv)
    except ValueError:
        return 99999


def dump_allowed_on_interval(interval: str) -> bool:
    """Дамп только на TF ≤ 1h (5m, 15m, 30m, 1h …)."""
    return interval_minutes(interval) <= 60
