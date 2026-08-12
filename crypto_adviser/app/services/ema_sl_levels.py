"""Расчёт SL (цена закрытия следующей свечи при EMA cross) — базовый и младший ТФ."""

from __future__ import annotations

from typing import Literal

from app.advisor.intervals import junior_interval_label, lower_kline_interval
from app.advisor.mtf import _bar_index_at_close
from app.advisor.tasks import AdvisorTask
from app.bybit.rest import BybitRest, _interval_to_ms
from app.indicators.ema_cross_price import next_close_ema_cross_price

LowerBarsCache = dict[tuple[str, str], list[tuple[int, float, float, float, float]]]


def sl_from_closes(
    client: BybitRest,
    symbol: str,
    closes: list[float],
    ema_fast: int,
    ema_slow: int,
) -> tuple[float | None, str | None]:
    if len(closes) < max(ema_fast, ema_slow) + 2:
        return None, "мало свечей"
    raw = next_close_ema_cross_price(closes, ema_fast, ema_slow)
    if raw is None:
        return None, "не рассчитано"
    if raw <= 0:
        return None, "некорректная цена"
    tick, _ = client.instrument_filters(symbol)
    return float(BybitRest.round_to_tick(raw, tick)), None


def junior_closes_for_sl(
    task: AdvisorTask,
    client: BybitRest,
    base_bars: list[tuple[int, float, float, float, float]],
    lower_bars_cache: LowerBarsCache,
    *,
    bar_index: int | None = None,
) -> list[float] | None:
    """Закрытия младшего ТФ на момент свечи bar_index (как МТФ в /zones)."""
    if not base_bars:
        return None
    if bar_index is None:
        bar_index = len(base_bars) - 1
    signal_open_ms = base_bars[bar_index][0]
    lower = lower_kline_interval(task.interval)

    if lower is None:
        if bar_index < 1:
            return None
        return [b[4] for b in base_bars[:bar_index]]

    cache_key = (task.symbol.upper(), lower)
    if cache_key not in lower_bars_cache:
        lower_bars_cache[cache_key] = client.closed_ohlc_bars_with_ts(
            task.symbol, lower, limit=250
        )
    lower_bars = lower_bars_cache[cache_key]
    if not lower_bars:
        return None

    step_ms = _interval_to_ms(task.interval)
    signal_close_ms = signal_open_ms + step_ms
    lower_step = _interval_to_ms(lower)
    junior_close_ms = signal_close_ms - lower_step
    j_idx = _bar_index_at_close(lower_bars, junior_close_ms, lower_step)
    if j_idx is None:
        return None
    return [b[4] for b in lower_bars[: j_idx + 1]]


def sl_pair_at_bar(
    task: AdvisorTask,
    client: BybitRest,
    base_bars: list[tuple[int, float, float, float, float]],
    lower_bars_cache: LowerBarsCache,
    *,
    bar_index: int | None = None,
) -> tuple[float | None, str | None, float | None, str | None]:
    if bar_index is None:
        bar_index = len(base_bars) - 1
    slice_bars = base_bars[: bar_index + 1]
    base_closes = [c for _, _, _, _, c in slice_bars]
    base_sl, base_err = sl_from_closes(
        client, task.symbol, base_closes, task.ema_fast, task.ema_slow
    )
    junior_closes = junior_closes_for_sl(
        task, client, base_bars, lower_bars_cache, bar_index=bar_index
    )
    if junior_closes is None:
        return base_sl, base_err, None, "нет данных младшего ТФ"
    mtf_sl, mtf_err = sl_from_closes(
        client, task.symbol, junior_closes, task.ema_fast, task.ema_slow
    )
    return base_sl, base_err, mtf_sl, mtf_err


def format_sl_values_line(
    task: AdvisorTask,
    base_sl: float | None,
    mtf_sl: float | None,
) -> str | None:
    if base_sl is None and mtf_sl is None:
        return None
    base_lbl = task.signal_interval_label()
    mtf_lbl = junior_interval_label(task.interval)
    b = f"{base_sl:g}" if base_sl is not None else "—"
    m = f"{mtf_sl:g}" if mtf_sl is not None else "—"
    return f"SL {base_lbl} – {b} · SL {mtf_lbl} – {m}"


SlTfMode = Literal["base", "junior"]


def sl_price_for_tf_mode(
    task: AdvisorTask,
    client: BybitRest,
    base_bars: list[tuple[int, float, float, float, float]],
    lower_bars_cache: LowerBarsCache,
    mode: SlTfMode,
    *,
    bar_index: int | None = None,
) -> tuple[float | None, str | None]:
    if bar_index is None:
        bar_index = len(base_bars) - 1
    if mode == "base":
        closes = [c for _, _, _, _, c in base_bars[: bar_index + 1]]
        return sl_from_closes(
            client, task.symbol, closes, task.ema_fast, task.ema_slow
        )
    junior_closes = junior_closes_for_sl(
        task, client, base_bars, lower_bars_cache, bar_index=bar_index
    )
    if junior_closes is None:
        return None, "нет данных младшего ТФ"
    return sl_from_closes(
        client, task.symbol, junior_closes, task.ema_fast, task.ema_slow
    )


def follow_tf_interval(task: AdvisorTask, mode: SlTfMode) -> str:
    """Интервал свечи, по закрытию которой двигаем SL."""
    if mode == "base":
        return task.interval
    lower = lower_kline_interval(task.interval)
    return lower if lower is not None else task.interval


def follow_tf_label(task: AdvisorTask, mode: SlTfMode) -> str:
    if mode == "base":
        return task.signal_interval_label()
    return junior_interval_label(task.interval)


def format_sl_report_line(
    label: str,
    task: AdvisorTask,
    base_sl: float | None,
    mtf_sl: float | None,
) -> str:
    base_lbl = task.signal_interval_label()
    mtf_lbl = junior_interval_label(task.interval)
    b = f"{base_sl:g}" if base_sl is not None else "—"
    m = f"{mtf_sl:g}" if mtf_sl is not None else "—"
    return f"{label}: SL {base_lbl} – {b} · SL {mtf_lbl} – {m}"
