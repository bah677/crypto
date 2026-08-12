"""Trend Context: даунтренд 1D, глубина истории, lite-проверка молодых монет."""

from __future__ import annotations

from dataclasses import dataclass

from app.pump_scan.params import PumpScanParams

DataStatus = str  # insufficient_history | young_partial | partial | full


@dataclass(frozen=True)
class TrendContext:
    data_status: DataStatus
    is_downtrend_context: bool
    not_applicable: bool = False
    drawdown_from_high_pct: float | None = None
    days_since_high: int | None = None
    stack_bearish: bool | None = None
    price_below_emas: bool | None = None
    history_days: int | None = None


def _drawdown_and_days_since_high(highs: list[float], close_prev: float) -> tuple[float, int] | None:
    if not highs or close_prev <= 0:
        return None
    high_ref = max(highs)
    if high_ref <= 0:
        return None
    dd = (close_prev - high_ref) / high_ref * 100.0
    idx_high = max(range(len(highs)), key=lambda i: highs[i])
    days_since_high = (len(highs) - 1) - idx_high
    return dd, days_since_high


def _data_status(history_days: int, params: PumpScanParams) -> DataStatus:
    if history_days < params.min_bars_for_ema50:
        return "insufficient_history"
    if history_days < params.min_bars_for_ema100:
        return "young_partial"
    if history_days < params.min_bars_for_ema200:
        return "partial"
    return "full"


def evaluate_trend_context(
    *,
    history_days: int,
    close_prev: float,
    ema50: float | None,
    ema100: float | None,
    ema200: float | None,
    daily_highs: list[float],
    params: PumpScanParams,
) -> TrendContext | None:
    """Чистая логика Trend Context (без API). daily_highs — highs закрытых 1D-баров."""
    if close_prev <= 0 or not daily_highs:
        return None

    status = _data_status(history_days, params)

    if status == "insufficient_history":
        return TrendContext(
            data_status=status,
            is_downtrend_context=False,
            not_applicable=True,
            history_days=history_days,
        )

    if status == "young_partial":
        metrics = _drawdown_and_days_since_high(daily_highs, close_prev)
        if metrics is None or ema50 is None:
            return TrendContext(
                data_status=status,
                is_downtrend_context=False,
                history_days=history_days,
                stack_bearish=None,
                price_below_emas=None,
            )
        dd, days_since_high = metrics
        price_below = close_prev < ema50
        is_dt = (
            price_below
            and dd <= params.downtrend_min_drawdown_pct
            and days_since_high >= params.young_coin_min_days_since_high
        )
        return TrendContext(
            data_status=status,
            is_downtrend_context=is_dt,
            drawdown_from_high_pct=dd,
            days_since_high=days_since_high,
            stack_bearish=None,
            price_below_emas=price_below,
            history_days=history_days,
        )

    stack_bearish: bool | None = None
    price_below_emas: bool | None = None

    if status == "full":
        if ema50 is not None and ema100 is not None and ema200 is not None:
            stack_bearish = ema50 < ema100 < ema200
            price_below_emas = (
                close_prev < ema50 and close_prev < ema100 and close_prev < ema200
            )
        else:
            stack_bearish = False
            price_below_emas = False
    else:  # partial
        if ema50 is not None and ema100 is not None:
            stack_bearish = ema50 < ema100
            price_below_emas = close_prev < ema50 and close_prev < ema100
        else:
            stack_bearish = False
            price_below_emas = False

    w = max(5, params.trend_context_lookback_days)
    window_highs = daily_highs[-w:] if len(daily_highs) >= w else daily_highs
    metrics = _drawdown_and_days_since_high(window_highs, close_prev)
    if metrics is None:
        return TrendContext(
            data_status=status,
            is_downtrend_context=False,
            stack_bearish=stack_bearish,
            price_below_emas=price_below_emas,
            history_days=history_days,
        )
    dd, days_since_high = metrics

    is_dt = (
        bool(stack_bearish)
        and bool(price_below_emas)
        and dd <= params.downtrend_min_drawdown_pct
        and days_since_high >= params.downtrend_min_days_since_high
    )
    return TrendContext(
        data_status=status,
        is_downtrend_context=is_dt,
        drawdown_from_high_pct=dd,
        days_since_high=days_since_high,
        stack_bearish=stack_bearish,
        price_below_emas=price_below_emas,
        history_days=history_days,
    )


def should_drop_by_downtrend_filter(trend: TrendContext, mode: str) -> bool:
    """True — hit отбрасывается модулем Trend Context."""
    m = (mode or "filter").strip().lower()
    if m != "filter":
        return False
    if trend.not_applicable:
        return False
    return not trend.is_downtrend_context


def format_trend_alert_line(trend: TrendContext, params: PumpScanParams) -> str | None:
    if trend.not_applicable:
        return (
            f"ℹ️ Недостаточно истории даже для EMA50 "
            f"(монета младше {params.min_bars_for_ema50}д) — трендовый контекст не определён"
        )
    if not trend.is_downtrend_context:
        return None
    dd = trend.drawdown_from_high_pct
    days = trend.days_since_high
    dd_str = f"{dd:.0f}%" if dd is not None else "—"
    days_str = f"{days}д" if days is not None else "—"
    hist = trend.history_days

    if trend.data_status == "young_partial":
        return (
            f"🌱 Pump у молодой монеты (в даунтренде с листинга): "
            f"<b>{dd_str}</b> от ATH за <b>{days_str}</b> "
            f"(EMA100/200 недоступны, истории {hist}д)"
        )

    line = f"🎯 Pump в даунтренде: <b>{dd_str}</b> от хая за <b>{days_str}</b>"
    if trend.data_status == "partial" and hist is not None:
        line += f" (EMA200 недоступна, истории {hist}д)"
    return line
