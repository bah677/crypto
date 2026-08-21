"""Сила pump-импульса: 1–3 🔥 для алерта."""

from __future__ import annotations

from app.pump_scan.daily_ema import DailyEmaSnapshot
from app.pump_scan.detect import ScanHit
from app.pump_scan.timeframes import profile_for


def _move_ratio(hit: ScanHit) -> float:
    profile = profile_for(hit.interval)
    if profile is None:
        return abs(hit.price_change_pct) / 5.0
    thresh = profile.smooth_pct if hit.move_kind == "smooth" else profile.spike_pct
    return abs(hit.price_change_pct) / max(thresh, 1.0)


def _move_points(hit: ScanHit) -> int:
    ratio = _move_ratio(hit)
    pts = 0
    if ratio >= 1.15:
        pts += 1
    if ratio >= 1.8:
        pts += 1
    pct = abs(hit.price_change_pct)
    if pct >= 10:
        pts = max(pts, 1)
    if pct >= 15:
        pts = max(pts, 2)
    return min(2, pts)


def _rvol_points(rvol: float) -> int:
    if rvol >= 15:
        return 2
    if rvol >= 5:
        return 1
    return 0


def _ema_above_count(price: float, emas: DailyEmaSnapshot | None) -> int:
    if emas is None:
        return 0
    return sum(
        1 for v in (emas.ema50, emas.ema100, emas.ema200) if v is not None and price > v
    )


def classify_pump_strength(hit: ScanHit, price: float, emas: DailyEmaSnapshot | None) -> int:
    """
    1–3 огонька (сумма 0–6 баллов):

    Ход:  ratio≥1.15 / ≥1.8 и абс. ≥10% / ≥15%  → 0–2
    RVOL: ≥5 / ≥15                              → 0–2
    EMA:  цена импульса выше 1 / 2+ EMA 1D      → 0–2

    0–2 → 🔥 · 3–4 → 🔥🔥 · 5–6 → 🔥🔥🔥
    """
    ema_n = _ema_above_count(price, emas)
    ema_pts = 2 if ema_n >= 2 else (1 if ema_n >= 1 else 0)
    total = _move_points(hit) + _rvol_points(hit.rvol) + ema_pts
    if total <= 2:
        return 1
    if total <= 4:
        return 2
    return 3


def pump_fire_prefix(strength: int) -> str:
    return "🔥" * max(1, min(3, strength))
