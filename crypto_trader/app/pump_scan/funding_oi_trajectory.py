"""Funding + OI Trajectory: композитный индикатор (ТЗ)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.pump_scan.params import PumpScanParams

FundingTrajectoryState = str  # no_extreme | extending | peak_reversing | normalized | unknown
OiTrend = str  # rising | falling | flat | unknown
FundingConfidence = str  # high | medium | low


@dataclass(frozen=True)
class FundingOiTrajectoryContext:
    funding_trajectory_state: FundingTrajectoryState
    oi_trend: OiTrend
    funding_interval_hours: float | None = None
    funding_confidence: FundingConfidence | None = None
    funding_now: float | None = None
    funding_min: float | None = None
    oi_chg_over_window_pct: float | None = None
    score_multiplier: float = 1.0
    alert_line: str | None = None  # всегда заполняется при enabled


def funding_confidence_from_interval(interval_hours: float) -> FundingConfidence:
    h = float(interval_hours)
    if h <= 2:
        return "high"
    if h <= 4:
        return "medium"
    return "low"


def periods_for_lookback(lookback_hours: int, interval_hours: float) -> int:
    if interval_hours <= 0:
        return 1
    return max(2, min(200, math.ceil(lookback_hours / interval_hours)))


def _is_non_decreasing_with_noise(values: list[float], noise_tolerance: float) -> bool:
    if len(values) < 2:
        return False
    tol = max(0.0, float(noise_tolerance))
    for i in range(1, len(values)):
        if values[i] < values[i - 1] - tol:
            return False
    return True


def classify_funding_trajectory(
    funding_series: list[float],
    *,
    extreme_threshold_pct: float,
    recovery_min_periods: int,
    noise_tolerance_pct: float,
    normalized_threshold_pct: float,
) -> FundingTrajectoryState:
    if not funding_series:
        return "unknown"

    funding_now = funding_series[-1]
    funding_min = min(funding_series)
    funding_min_idx = max(i for i, v in enumerate(funding_series) if v == funding_min)
    periods_count = len(funding_series)

    if abs(funding_min) < extreme_threshold_pct:
        return "no_extreme"

    if funding_min_idx == periods_count - 1:
        return "extending"

    tail = funding_series[funding_min_idx:]
    is_recovering = _is_non_decreasing_with_noise(tail, noise_tolerance_pct)
    periods_since_extreme = periods_count - 1 - funding_min_idx

    if is_recovering and periods_since_extreme >= max(1, recovery_min_periods):
        if abs(funding_now) <= normalized_threshold_pct:
            return "normalized"
        return "peak_reversing"
    return "extending"


def classify_oi_trend(
    oi_series: list[float],
    *,
    flat_threshold_pct: float,
) -> OiTrend:
    if len(oi_series) < 2:
        return "unknown"
    oi0 = oi_series[0]
    oi1 = oi_series[-1]
    if oi0 <= 0:
        return "unknown"
    chg = (oi1 - oi0) / oi0 * 100.0
    thr = abs(float(flat_threshold_pct))
    if chg <= -thr:
        return "falling"
    if chg >= thr:
        return "rising"
    return "flat"


def funding_oi_score_multiplier(
    funding_state: FundingTrajectoryState,
    oi_trend: OiTrend,
    params: PumpScanParams,
) -> float:
    if funding_state == "unknown" and oi_trend == "unknown":
        return 1.0
    if funding_state == "peak_reversing" and oi_trend in ("falling", "flat"):
        return max(0.1, params.funding_oi_score_bonus_best)
    if funding_state == "extending" and oi_trend == "rising":
        return max(0.0, min(params.funding_oi_score_penalty_worst, 1.0))
    if funding_state == "normalized":
        return max(0.0, min(params.funding_oi_score_penalty_late, 1.0))
    return 1.0


def _fmt_funding_pct(v: float) -> str:
    return f"{v:.0f}%"


def _oi_trend_ru(trend: OiTrend) -> str:
    return {
        "falling": "падает",
        "flat": "плоский",
        "rising": "растёт",
        "unknown": "неизвестен",
    }.get(trend, trend)


def build_trajectory_alert_line(
    *,
    funding_state: FundingTrajectoryState,
    oi_trend: OiTrend,
    funding_now: float | None,
    funding_min: float | None,
    funding_interval_hours: float | None,
    funding_confidence: FundingConfidence | None,
    extreme_threshold_pct: float = 1000.0,
) -> str:
    """Всегда возвращает строку для алерта (в т.ч. no_extreme / unknown)."""
    conf_suffix = ""
    if funding_confidence and funding_confidence != "high" and funding_interval_hours:
        ih = funding_interval_hours
        ih_s = f"{int(ih)}" if abs(ih - round(ih)) < 0.01 else f"{ih:.1f}"
        conf_suffix = f" (интервал фандинга {ih_s}ч, точность траектории: {funding_confidence})"

    now_s = _fmt_funding_pct(funding_now) if funding_now is not None else "—"
    min_s = _fmt_funding_pct(funding_min) if funding_min is not None else "—"
    oi_s = _oi_trend_ru(oi_trend)
    thr_s = _fmt_funding_pct(extreme_threshold_pct)

    if funding_state == "unknown":
        return (
            "⚪ Funding+OI: данные недоступны — по траектории сигнал на вход "
            "не оценивался"
        )

    if funding_state == "no_extreme":
        return (
            f"⚪ Funding+OI: экстремума фандинга нет (сейчас {now_s}, min {min_s}, "
            f"порог ≥{thr_s}) — по траектории сигнала на вход нет; "
            f"OI {oi_s}{conf_suffix}"
        )

    if funding_state == "peak_reversing":
        if oi_trend in ("falling", "flat"):
            return (
                f"🟢 Сквиз выжат: фандинг разворачивается ({now_s}, было {min_s}), "
                f"OI {oi_s} — целевое окно входа{conf_suffix}"
            )
        if oi_trend == "unknown":
            return (
                f"🟢 Фандинг разворачивается ({now_s}, было {min_s}) — "
                f"по фандингу окно входа, OI неизвестен{conf_suffix}"
            )
        if oi_trend == "rising":
            return (
                f"🟡 Фандинг разворачивается ({now_s}, было {min_s}), "
                f"но OI снова растёт — смешанно, вход осторожно{conf_suffix}"
            )

    if funding_state == "extending":
        if oi_trend == "rising":
            return (
                f"🔴 Фандинг на экстремуме ({now_s}) и OI растёт — "
                f"риск продолжения, вход рано{conf_suffix}"
            )
        return (
            f"🟡 Сквиз в разгаре: фандинг у экстремума ({now_s}) — "
            f"рано для входа, OI {oi_s}{conf_suffix}"
        )

    if funding_state == "normalized":
        return (
            f"⚪ Фандинг уже нормализовался ({now_s}) — сквиз, вероятно, завершён; "
            f"по траектории вход поздний{conf_suffix}"
        )

    return (
        f"⚪ Funding+OI: состояние {funding_state}, OI {oi_s} — "
        f"по траектории сигнала на вход нет{conf_suffix}"
    )


def evaluate_funding_oi_trajectory(
    *,
    funding_series: list[float] | None,
    oi_series: list[float] | None,
    funding_interval_hours: float | None,
    params: PumpScanParams,
) -> FundingOiTrajectoryContext | None:
    if not params.funding_trajectory_enabled:
        return None

    f_state: FundingTrajectoryState = "unknown"
    o_trend: OiTrend = "unknown"
    f_now: float | None = None
    f_min: float | None = None
    oi_chg: float | None = None
    conf: FundingConfidence | None = None
    interval_h = funding_interval_hours

    if funding_series:
        f_state = classify_funding_trajectory(
            funding_series,
            extreme_threshold_pct=params.funding_extreme_threshold_pct,
            recovery_min_periods=params.funding_recovery_min_periods,
            noise_tolerance_pct=params.funding_noise_tolerance_pct,
            normalized_threshold_pct=params.funding_normalized_threshold_pct,
        )
        f_now = funding_series[-1]
        f_min = min(funding_series)
        if interval_h and interval_h > 0:
            conf = funding_confidence_from_interval(interval_h)

    if oi_series and len(oi_series) >= 2:
        o_trend = classify_oi_trend(
            oi_series, flat_threshold_pct=params.oi_trend_flat_threshold_pct
        )
        o0 = oi_series[0]
        o1 = oi_series[-1]
        if o0 > 0:
            oi_chg = (o1 - o0) / o0 * 100.0

    mult = funding_oi_score_multiplier(f_state, o_trend, params)
    line = build_trajectory_alert_line(
        funding_state=f_state,
        oi_trend=o_trend,
        funding_now=f_now,
        funding_min=f_min,
        funding_interval_hours=interval_h,
        funding_confidence=conf,
        extreme_threshold_pct=params.funding_extreme_threshold_pct,
    )
    return FundingOiTrajectoryContext(
        funding_trajectory_state=f_state,
        oi_trend=o_trend,
        funding_interval_hours=interval_h,
        funding_confidence=conf,
        funding_now=f_now,
        funding_min=f_min,
        oi_chg_over_window_pct=oi_chg,
        score_multiplier=mult,
        alert_line=line,
    )
