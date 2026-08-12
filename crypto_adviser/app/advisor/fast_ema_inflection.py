"""Перелом наклона fast EMA: всегда на 5m, подтверждение второй 5m-свечой."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.advisor.mtf import (
    format_mtf_lines,
    junior_zone_at_signal,
    senior_zone_at_signal,
)
from app.advisor.tasks import AdvisorTask
from app.bybit.rest import BybitRest, _interval_to_ms
from app.indicators.ema import ema_series

LowerBarsCache = dict[tuple[str, str], list[tuple[int, float, float, float, float]]]

TREND_TF = "5"
_KLINE_LIMIT = 150
MSK = ZoneInfo("Europe/Moscow")

_last_confirmed_bar_ms: dict[int, int] = {}


@dataclass(frozen=True)
class InflectionSignal:
    confirm_open_ms: int
    message: str


def _format_bar_time_msk(open_ms: int) -> str:
    dt = datetime.fromtimestamp(open_ms / 1000, tz=MSK)
    return dt.strftime("%Y-%m-%d %H:%M MSK")


def _min_delta(price: float, tick: float) -> float:
    if tick > 0:
        return float(tick)
    return max(abs(price) * 1e-5, 1e-8)


def _delta(fast: list[float | None], i: int) -> float | None:
    if i < 1 or i >= len(fast):
        return None
    a, b = fast[i - 1], fast[i]
    if a is None or b is None:
        return None
    return b - a


def _inflection_up_confirmed(
    fast: list[float | None], confirm_index: int, eps: float
) -> bool:
    if confirm_index < 3:
        return False
    d_old = _delta(fast, confirm_index - 2)
    d_inf = _delta(fast, confirm_index - 1)
    d_conf = _delta(fast, confirm_index)
    if d_old is None or d_inf is None or d_conf is None:
        return False
    return d_old <= -eps and d_inf > eps and d_conf > eps


def _inflection_down_confirmed(
    fast: list[float | None], confirm_index: int, eps: float
) -> bool:
    if confirm_index < 3:
        return False
    d_old = _delta(fast, confirm_index - 2)
    d_inf = _delta(fast, confirm_index - 1)
    d_conf = _delta(fast, confirm_index)
    if d_old is None or d_inf is None or d_conf is None:
        return False
    return d_old >= eps and d_inf < -eps and d_conf < -eps


def _zone_at(
    fast: list[float | None], slow: list[float | None], bar_index: int
) -> tuple[str, str] | None:
    f, s = fast[bar_index], slow[bar_index]
    if f is None or s is None:
        return None
    if f > s:
        return ("🟢", "Покупка")
    if f < s:
        return ("🔴", "Продажа")
    return ("⚪️", "нейтрально")


def _last_closed_base_bar_index(
    base_bars: list[tuple[int, float, float, float, float]],
    at_5m_close_ms: int,
    base_interval: str,
) -> int | None:
    """Последняя закрытая свеча базового ТФ задания к моменту close 5m."""
    step = _interval_to_ms(base_interval)
    best: int | None = None
    for i, (open_ms, *_rest) in enumerate(base_bars):
        if open_ms + step <= at_5m_close_ms:
            best = i
    return best


def _format_inflection_message(
    task: AdvisorTask,
    *,
    bar_label: str,
    zone_emoji: str,
    zone_side: str,
    trend_line: str,
    mtf_lines: list[str],
) -> str:
    tf = task.signal_interval_label()
    alias = task.alias.strip()
    if alias:
        head = f"Изменение тренда · {alias} ({task.symbol}) · {tf}"
    else:
        head = f"Изменение тренда · {task.symbol} · {tf}"
    lines = [
        head,
        f"{trend_line} · зона 5m {zone_emoji} {zone_side}",
        f"EMA {task.ema_fast}/{task.ema_slow} · 5m свеча {bar_label}",
    ]
    lines.extend(mtf_lines)
    return "\n".join(lines)


def detect_fast_ema_inflection(
    task: AdvisorTask,
    *,
    client: BybitRest,
    lower_bars_cache: LowerBarsCache,
) -> InflectionSignal | None:
    """
    Сила тренда (перелом fast EMA) всегда на 5m; зоны СТФ/МТФ — по ТФ задания.
    """
    if task.db_id is None:
        return None

    need = max(task.ema_fast, task.ema_slow) + 5
    limit = min(_KLINE_LIMIT, max(80, need + 20))

    bars_5m = client.closed_ohlc_bars_with_ts(task.symbol, TREND_TF, limit=limit)
    if not bars_5m or len(bars_5m) < need:
        return None

    idx = len(bars_5m) - 1
    if idx < 3:
        return None

    closes = [b[4] for b in bars_5m]
    fast = ema_series(closes, task.ema_fast)
    slow = ema_series(closes, task.ema_slow)
    zone = _zone_at(fast, slow, idx)
    if zone is None:
        return None

    tick, _ = client.instrument_filters(task.symbol)
    eps = _min_delta(closes[idx], float(tick))

    zone_emoji, zone_side = zone
    up_ok = _inflection_up_confirmed(fast, idx, eps)
    down_ok = _inflection_down_confirmed(fast, idx, eps)

    trend_line: str | None = None
    if zone_side == "Продажа":
        if up_ok:
            trend_line = "тренд слабеет"
        elif down_ok:
            trend_line = "тренд усиливается"
    elif zone_side == "Покупка":
        if down_ok:
            trend_line = "тренд слабеет"
        elif up_ok:
            trend_line = "тренд усиливается"

    if trend_line is None:
        return None

    open_ms = bars_5m[idx][0]
    if _last_confirmed_bar_ms.get(task.db_id) == open_ms:
        return None

    close_5m_ms = open_ms + _interval_to_ms(TREND_TF)
    bar_label = _format_bar_time_msk(open_ms)

    mtf_lines: list[str] = []
    if task.interval != TREND_TF:
        base_bars = client.closed_ohlc_bars_with_ts(
            task.symbol, task.interval, limit=limit
        )
        base_idx = _last_closed_base_bar_index(base_bars, close_5m_ms, task.interval)
        if base_idx is not None:
            base_open = base_bars[base_idx][0]
            senior = senior_zone_at_signal(
                base_bars, base_idx, task.interval, task.ema_fast, task.ema_slow
            )
            junior = junior_zone_at_signal(
                task.interval,
                base_bars,
                base_idx,
                base_open,
                task.ema_fast,
                task.ema_slow,
                client,
                task.symbol,
                lower_bars_cache,
            )
            mtf_lines = format_mtf_lines(senior, junior)
    else:
        senior = senior_zone_at_signal(
            bars_5m, idx, task.interval, task.ema_fast, task.ema_slow
        )
        junior = junior_zone_at_signal(
            task.interval,
            bars_5m,
            idx,
            open_ms,
            task.ema_fast,
            task.ema_slow,
            client,
            task.symbol,
            lower_bars_cache,
        )
        mtf_lines = format_mtf_lines(senior, junior)

    message = _format_inflection_message(
        task,
        bar_label=bar_label,
        zone_emoji=zone_emoji,
        zone_side=zone_side,
        trend_line=trend_line,
        mtf_lines=mtf_lines,
    )
    _last_confirmed_bar_ms[task.db_id] = open_ms
    return InflectionSignal(confirm_open_ms=open_ms, message=message)
