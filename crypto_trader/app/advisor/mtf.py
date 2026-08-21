from __future__ import annotations

from app.advisor.intervals import higher_aggregate_ratio, lower_kline_interval
from app.bybit.rest import BybitRest, _interval_to_ms
from app.indicators.ema import ema_series


def _ema_min_bars(ema_fast: int, ema_slow: int) -> int:
    return max(ema_fast, ema_slow) + 5


def _aggregate_close(chunk: list[tuple[float, float, float, float]]) -> float:
    return chunk[-1][3]


def synthetic_close_series(
    ohlc_bars: list[tuple[int, float, float, float, float]],
    ratio: int,
) -> list[float]:
    """Закрытия синтетических свечей, окно ratio базовых баров, конец — close сигнальной."""
    out: list[float] = []
    for i in range(len(ohlc_bars)):
        if i < ratio - 1:
            continue
        chunk = [(b[1], b[2], b[3], b[4]) for b in ohlc_bars[i - ratio + 1 : i + 1]]
        out.append(_aggregate_close(chunk))
    return out


def ema_zone(closes: list[float], ema_fast: int, ema_slow: int) -> tuple[str, str] | None:
    need = _ema_min_bars(ema_fast, ema_slow)
    if len(closes) < need:
        return None
    fast = ema_series(closes, ema_fast)
    slow = ema_series(closes, ema_slow)
    f1, s1 = fast[-1], slow[-1]
    if f1 is None or s1 is None:
        return None
    if f1 > s1:
        return ("🟢", "Покупка")
    if f1 < s1:
        return ("🔴", "Продажа")
    return ("⚪️", "нейтрально")


def _bar_index_at_close(
    bars: list[tuple[int, float, float, float, float]], close_ms: int, step_ms: int
) -> int | None:
    for i, (open_ms, *_rest) in enumerate(bars):
        if open_ms + step_ms == close_ms:
            return i
    return None


def senior_zone_at_signal(
    ohlc_bars: list[tuple[int, float, float, float, float]],
    bar_index: int,
    interval: str,
    ema_fast: int,
    ema_slow: int,
) -> tuple[str, str] | None:
    ratio = higher_aggregate_ratio(interval)
    if bar_index < ratio - 1:
        return None
    syn = synthetic_close_series(ohlc_bars[: bar_index + 1], ratio)
    return ema_zone(syn, ema_fast, ema_slow)


def junior_zone_at_signal(
    task_interval: str,
    ohlc_bars: list[tuple[int, float, float, float, float]],
    bar_index: int,
    signal_open_ms: int,
    ema_fast: int,
    ema_slow: int,
    client: BybitRest,
    symbol: str,
    lower_bars_cache: dict[tuple[str, str], list[tuple[int, float, float, float, float]]],
) -> tuple[str, str] | None:
    step_ms = _interval_to_ms(task_interval)
    signal_close_ms = signal_open_ms + step_ms
    lower = lower_kline_interval(task_interval)

    if lower is None:
        if bar_index < 1:
            return None
        closes = [b[4] for b in ohlc_bars[:bar_index]]
        return ema_zone(closes, ema_fast, ema_slow)

    cache_key = (symbol.upper(), lower)
    if cache_key not in lower_bars_cache:
        lower_bars_cache[cache_key] = client.closed_ohlc_bars_with_ts(
            symbol, lower, limit=250
        )
    lower_bars = lower_bars_cache[cache_key]
    if not lower_bars:
        return None

    lower_step = _interval_to_ms(lower)
    junior_close_ms = signal_close_ms - lower_step
    j_idx = _bar_index_at_close(lower_bars, junior_close_ms, lower_step)
    if j_idx is None:
        return None
    closes = [b[4] for b in lower_bars[: j_idx + 1]]
    return ema_zone(closes, ema_fast, ema_slow)


def format_zone_line(zone: tuple[str, str] | None) -> str:
    if zone is None:
        return "— нет данных"
    emoji, side = zone
    if side == "Покупка":
        return f"{emoji} Зона покупок"
    if side == "Продажа":
        return f"{emoji} Зона продаж"
    return f"{emoji} {side}"


def format_mtf_lines(
    senior: tuple[str, str] | None,
    junior: tuple[str, str] | None,
) -> list[str]:
    lines: list[str] = []
    if senior is not None:
        lines.append(f"ℹ️ на старшем ТФ в зоне {senior[0]} {senior[1]}")
    if junior is not None:
        lines.append(f"ℹ️ на младшем ТФ в зоне {junior[0]} {junior[1]}")
    return lines
