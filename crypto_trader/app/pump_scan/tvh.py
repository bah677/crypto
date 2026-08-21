"""ТВХ после импульса: разворот (фейд) и продолжение на младшем TF + score."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.indicators.ema import ema_series
from app.pump_scan.timeframes import interval_label
from app.scalp_advisor.patterns import (
    bearish_engulfing,
    bearish_pin_bar,
    bullish_engulfing,
    bullish_pin_bar,
)

Bar = tuple[int, float, float, float, float, float]
ImpulseDirection = Literal["pump", "dump"]
TvhScenario = Literal["short_fade", "long_continue", "long_fade", "short_continue"]

_JUNIOR_MAP: dict[str, str] = {
    "D": "240",
    "240": "60",
    "120": "30",
    "60": "15",
    "30": "5",
    "15": "5",
    "5": "1",
    "3": "1",
    "1": "1",
}


def junior_interval(source_interval: str) -> str:
    return _JUNIOR_MAP.get(source_interval.upper(), "5")


@dataclass(frozen=True)
class ImpulseContext:
    symbol: str
    direction: ImpulseDirection
    source_interval: str
    entry_interval: str
    impulse_low: float
    impulse_high: float
    impulse_pct: float
    impulse_rvol: float
    move_kind: str = "spike"
    impulse_bar_open_ms: int = 0


def _first_eval_bar_index(bars: list[Bar], impulse_open_ms: int) -> int:
    """Первая свеча младшего TF, открывшаяся не раньше импульса."""
    if impulse_open_ms <= 0:
        return 0
    for i, (t, *_) in enumerate(bars):
        if t >= impulse_open_ms:
            return i
    return len(bars)


def _resolve_bar_idx(
    bars: list[Bar],
    ctx: ImpulseContext,
    params: TvhParams,
    bar_idx: int | None,
) -> int | None:
    if len(bars) < params.ema_slow + 5:
        return None
    idx = (len(bars) - 1) if bar_idx is None else bar_idx
    if idx < 0 or idx >= len(bars):
        return None
    first = _first_eval_bar_index(bars, ctx.impulse_bar_open_ms)
    if idx < first:
        return None
    return idx


@dataclass(frozen=True)
class TvhCandidate:
    scenario: TvhScenario
    score: int
    entry_low: float
    entry_high: float
    invalidation: float
    entry_interval: str
    reasons: list[str] = field(default_factory=list)
    score_parts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class TvhParams:
    min_score: int = 45
    ema_fast: int = 9
    ema_slow: int = 21
    min_retrace_fade: float = 0.08
    pullback_min: float = 0.18
    pullback_max: float = 0.58
    swing_lookback: int = 6


def _fade_confirmed(
    parts: dict[str, int], retrace: float, min_retrace: float
) -> bool:
    """Подтверждение фейда: быстрый pump-dump (15–30 мин)."""
    return (
        parts.get("structure", 0) > 0
        or parts.get("pattern", 0) >= 20
        or (parts.get("exhaustion", 0) >= 12 and retrace >= min_retrace)
        or (parts.get("volume", 0) >= 10 and retrace >= min_retrace)
        or (parts.get("ema", 0) >= 15 and retrace >= min_retrace * 1.15)
    )


def _body(o: float, c: float) -> float:
    return abs(c - o)


def _upper_wick(o: float, h: float, c: float) -> float:
    return h - max(o, c)


def _lower_wick(o: float, l: float, c: float) -> float:
    return min(o, c) - l


def _range_size(ctx: ImpulseContext) -> float:
    return max(ctx.impulse_high - ctx.impulse_low, 1e-12)


def _retrace_from_top(close: float, ctx: ImpulseContext) -> float:
    return (ctx.impulse_high - close) / _range_size(ctx)


def _retrace_from_bottom(close: float, ctx: ImpulseContext) -> float:
    return (close - ctx.impulse_low) / _range_size(ctx)


def _candle_info(bar: Bar) -> tuple[float, float, float, float, float]:
    _, o, h, l, c, _ = bar
    return o, h, l, c, _body(o, c)


def _ema_delta(fast: list[float | None], idx: int) -> float | None:
    if idx < 1 or idx >= len(fast):
        return None
    a, b = fast[idx - 1], fast[idx]
    if a is None or b is None:
        return None
    return b - a


def _inflection_down(fast: list[float | None], idx: int, eps: float) -> bool:
    if idx < 3:
        return False
    d0 = _ema_delta(fast, idx - 2)
    d1 = _ema_delta(fast, idx - 1)
    d2 = _ema_delta(fast, idx)
    if d0 is None or d1 is None or d2 is None:
        return False
    return d0 >= eps and d1 < -eps and d2 < -eps


def _inflection_up(fast: list[float | None], idx: int, eps: float) -> bool:
    if idx < 3:
        return False
    d0 = _ema_delta(fast, idx - 2)
    d1 = _ema_delta(fast, idx - 1)
    d2 = _ema_delta(fast, idx)
    if d0 is None or d1 is None or d2 is None:
        return False
    return d0 <= -eps and d1 > eps and d2 > eps


def _sell_pressure(bars: list[Bar], n: int = 3) -> float:
    tail = bars[-n:]
    green = red = 0.0
    for _, o, _, _, c, vol in tail:
        if c < o:
            red += vol
        else:
            green += vol
    if green <= 1e-12:
        return 10.0 if red > 0 else 0.0
    return red / green


def _buy_pressure(bars: list[Bar], n: int = 3) -> float:
    tail = bars[-n:]
    green = red = 0.0
    for _, o, _, _, c, vol in tail:
        if c >= o:
            green += vol
        else:
            red += vol
    if red <= 1e-12:
        return 10.0 if green > 0 else 0.0
    return green / red


def _swing_low(bars: list[Bar], end_idx: int, lookback: int) -> float | None:
    start = max(0, end_idx - lookback)
    chunk = bars[start:end_idx]
    if not chunk:
        return None
    return min(b[3] for b in chunk)


def _swing_high(bars: list[Bar], end_idx: int, lookback: int) -> float | None:
    start = max(0, end_idx - lookback)
    chunk = bars[start:end_idx]
    if not chunk:
        return None
    return max(b[2] for b in chunk)


def _fmt_price(px: float) -> str:
    if px >= 100:
        return f"{px:.2f}"
    if px >= 1:
        return f"{px:.4f}"
    return f"{px:.6f}"


def _zone_around(price: float, width_pct: float = 0.003) -> tuple[float, float]:
    w = max(price * width_pct, price * 1e-4)
    return price - w, price + w


def _zone_around(price: float, width_pct: float = 0.003) -> tuple[float, float]:
    w = max(price * width_pct, price * 1e-4)
    return price - w, price + w


def _preview_candidate(
    scenario: TvhScenario,
    score: int,
    ctx: ImpulseContext,
    parts: dict[str, int],
    reasons: list[str],
) -> TvhCandidate:
    return TvhCandidate(
        scenario=scenario,
        score=score,
        entry_low=0.0,
        entry_high=0.0,
        invalidation=0.0,
        entry_interval=ctx.entry_interval,
        reasons=reasons,
        score_parts=parts,
    )


def preview_watch_scores(
    bars: list[Bar],
    ctx: ImpulseContext,
    params: TvhParams | None = None,
    *,
    bar_idx: int | None = None,
) -> tuple[int | None, int | None]:
    """Текущий score шорт/лонг (EMA по всей истории, сигнал после импульса)."""
    p = params or TvhParams()
    idx = _resolve_bar_idx(bars, ctx, p, bar_idx)
    if idx is None:
        return None, None
    if ctx.direction == "pump":
        short_c = _eval_fade_short_after_pump(bars, ctx, p, preview=True, bar_idx=idx)
        long_c = _eval_continue_long_after_pump(bars, ctx, p, preview=True, bar_idx=idx)
    else:
        short_c = _eval_continue_short_after_dump(bars, ctx, p, preview=True, bar_idx=idx)
        long_c = _eval_fade_long_after_dump(bars, ctx, p, preview=True, bar_idx=idx)
    return (
        short_c.score if short_c is not None else 0,
        long_c.score if long_c is not None else 0,
    )


def _eval_fade_short_after_pump(
    bars: list[Bar],
    ctx: ImpulseContext,
    params: TvhParams,
    *,
    preview: bool = False,
    bar_idx: int | None = None,
) -> TvhCandidate | None:
    idx = _resolve_bar_idx(bars, ctx, params, bar_idx)
    if idx is None:
        return None
    _, _, _, close, body = _candle_info(bars[idx])
    retrace = _retrace_from_top(close, ctx)
    if retrace < params.min_retrace_fade and not preview:
        return None

    parts: dict[str, int] = {}
    reasons: list[str] = []

    o, h, l, c, body = _candle_info(bars[idx])
    prev = bars[idx - 1]
    po, ph, pl, pc, _ = _candle_info(prev)

    if bearish_engulfing((po, ph, pl, pc), (o, h, l, c)):
        parts["pattern"] = 25
        reasons.append("медвежье поглощение")
    elif bearish_pin_bar(o, h, l, c):
        parts["pattern"] = 20
        reasons.append("медвежий пин-бар")

    if c < o and body > 1e-12:
        wick = _upper_wick(o, h, c) / body
        if wick >= 0.45:
            parts["exhaustion"] = max(parts.get("exhaustion", 0), 18)
            reasons.append("верхний фитиль / истощение")

    if _sell_pressure(bars[: idx + 1], 3) >= 1.25:
        parts["volume"] = 12
        reasons.append("давление продаж")

    swing = _swing_low(bars, idx, params.swing_lookback)
    if swing is not None and close < swing:
        parts["structure"] = 20
        reasons.append("пробой локального минимума")

    closes = [b[4] for b in bars]
    fast = ema_series(closes, params.ema_fast)
    eps = max(close * 1e-5, 1e-8)
    if _inflection_down(fast, idx, eps):
        parts["ema"] = 15
        reasons.append(f"перелом EMA{params.ema_fast} вниз")

    if len(bars) >= 4 and idx >= 3:
        last3 = bars[idx - 2 : idx + 1]
        if all(b[4] >= b[1] for b in last3) and close >= ctx.impulse_high * 0.995:
            parts["extension"] = -20
            reasons.append("импульс ещё тянется")

    score = max(0, min(100, sum(parts.values())))
    confirmed = _fade_confirmed(parts, retrace, params.min_retrace_fade)
    if preview:
        return _preview_candidate("short_fade", score, ctx, parts, reasons)
    if score < params.min_score or not confirmed:
        return None

    inv = ctx.impulse_high * 1.002
    z_low = ctx.impulse_high - _range_size(ctx) * 0.5
    z_high = ctx.impulse_high - _range_size(ctx) * 0.38
    if close < z_high:
        entry_low, entry_high = _zone_around(close)
    else:
        entry_low, entry_high = min(z_low, z_high), max(z_low, z_high)

    return TvhCandidate(
        scenario="short_fade",
        score=score,
        entry_low=entry_low,
        entry_high=entry_high,
        invalidation=inv,
        entry_interval=ctx.entry_interval,
        reasons=reasons,
        score_parts=parts,
    )


def _eval_continue_long_after_pump(
    bars: list[Bar],
    ctx: ImpulseContext,
    params: TvhParams,
    *,
    preview: bool = False,
    bar_idx: int | None = None,
) -> TvhCandidate | None:
    idx = _resolve_bar_idx(bars, ctx, params, bar_idx)
    if idx is None:
        return None
    _, _, _, close, _ = _candle_info(bars[idx])
    if close < ctx.impulse_low * 0.985:
        if preview:
            return _preview_candidate("long_continue", 0, ctx, {}, [])
        return None

    retrace = _retrace_from_top(close, ctx)
    if (retrace < params.pullback_min or retrace > params.pullback_max) and not preview:
        return None

    parts: dict[str, int] = {}
    reasons: list[str] = []
    if params.pullback_min <= retrace <= params.pullback_max:
        reasons.append(f"откат {retrace * 100:.0f}% импульса")

    o, h, l, c, body = _candle_info(bars[idx])
    prev = bars[idx - 1]
    po, ph, pl, pc, _ = _candle_info(prev)

    if bullish_engulfing((po, ph, pl, pc), (o, h, l, c)):
        parts["pattern"] = 25
        reasons.append("бычье поглощение")
    elif bullish_pin_bar(o, h, l, c):
        parts["pattern"] = 20
        reasons.append("бычий пин-бар")

    closes = [b[4] for b in bars]
    fast = ema_series(closes, params.ema_fast)
    slow = ema_series(closes, params.ema_slow)
    f, s = fast[idx], slow[idx]
    eps = max(close * 1e-5, 1e-8)
    if f is not None and abs(close - f) / close <= 0.004:
        parts["ema_touch"] = 15
        reasons.append(f"отбой от EMA{params.ema_fast}")
    if f is not None and s is not None and f > s:
        parts["trend"] = 12
        reasons.append("fast EMA выше slow")
    if _inflection_up(fast, idx, eps):
        parts["ema_turn"] = 10
        reasons.append("наклон EMA вверх")

    if _buy_pressure(bars[: idx + 1], 3) >= 1.2:
        parts["volume"] = 10
        reasons.append("покупки на откате")

    if close < ctx.impulse_low:
        if preview:
            return _preview_candidate("long_continue", 0, ctx, parts, reasons)
        return None

    score = max(0, min(100, sum(parts.values())))
    confirmed = parts.get("pattern", 0) >= 20 or (
        parts.get("ema_touch", 0) > 0 and retrace >= params.pullback_min
    )
    if preview:
        return _preview_candidate("long_continue", score, ctx, parts, reasons)
    if score < params.min_score or not confirmed:
        return None

    entry_low, entry_high = _zone_around(close)
    inv = ctx.impulse_low * 0.998
    return TvhCandidate(
        scenario="long_continue",
        score=score,
        entry_low=entry_low,
        entry_high=entry_high,
        invalidation=inv,
        entry_interval=ctx.entry_interval,
        reasons=reasons,
        score_parts=parts,
    )


def _eval_fade_long_after_dump(
    bars: list[Bar],
    ctx: ImpulseContext,
    params: TvhParams,
    *,
    preview: bool = False,
    bar_idx: int | None = None,
) -> TvhCandidate | None:
    idx = _resolve_bar_idx(bars, ctx, params, bar_idx)
    if idx is None:
        return None
    _, _, _, close, _ = _candle_info(bars[idx])
    retrace = _retrace_from_bottom(close, ctx)
    if retrace < params.min_retrace_fade and not preview:
        return None

    parts: dict[str, int] = {}
    reasons: list[str] = []
    o, h, l, c, body = _candle_info(bars[idx])
    prev = bars[idx - 1]
    po, ph, pl, pc, _ = _candle_info(prev)

    if bullish_engulfing((po, ph, pl, pc), (o, h, l, c)):
        parts["pattern"] = 25
        reasons.append("бычье поглощение")
    elif bullish_pin_bar(o, h, l, c):
        parts["pattern"] = 20
        reasons.append("бычий пин-бар")

    if c >= o and body > 1e-12:
        wick = _lower_wick(o, l, c) / body
        if wick >= 0.45:
            parts["exhaustion"] = max(parts.get("exhaustion", 0), 18)
            reasons.append("нижний фитиль / истощение")

    if _buy_pressure(bars[: idx + 1], 3) >= 1.25:
        parts["volume"] = 12
        reasons.append("давление покупок")

    swing = _swing_high(bars, idx, params.swing_lookback)
    if swing is not None and close > swing:
        parts["structure"] = 20
        reasons.append("пробой локального максимума")

    closes = [b[4] for b in bars]
    fast = ema_series(closes, params.ema_fast)
    eps = max(close * 1e-5, 1e-8)
    if _inflection_up(fast, idx, eps):
        parts["ema"] = 15
        reasons.append(f"перелом EMA{params.ema_fast} вверх")

    score = max(0, min(100, sum(parts.values())))
    confirmed = _fade_confirmed(parts, retrace, params.min_retrace_fade)
    if preview:
        return _preview_candidate("long_fade", score, ctx, parts, reasons)
    if score < params.min_score or not confirmed:
        return None

    inv = ctx.impulse_low * 0.998
    z_low = ctx.impulse_low + _range_size(ctx) * 0.38
    z_high = ctx.impulse_low + _range_size(ctx) * 0.5
    if close > z_low:
        entry_low, entry_high = _zone_around(close)
    else:
        entry_low, entry_high = min(z_low, z_high), max(z_low, z_high)

    return TvhCandidate(
        scenario="long_fade",
        score=score,
        entry_low=entry_low,
        entry_high=entry_high,
        invalidation=inv,
        entry_interval=ctx.entry_interval,
        reasons=reasons,
        score_parts=parts,
    )


def _eval_continue_short_after_dump(
    bars: list[Bar],
    ctx: ImpulseContext,
    params: TvhParams,
    *,
    preview: bool = False,
    bar_idx: int | None = None,
) -> TvhCandidate | None:
    idx = _resolve_bar_idx(bars, ctx, params, bar_idx)
    if idx is None:
        return None
    _, _, _, close, _ = _candle_info(bars[idx])
    if close > ctx.impulse_high * 1.015:
        if preview:
            return _preview_candidate("short_continue", 0, ctx, {}, [])
        return None

    retrace = _retrace_from_bottom(close, ctx)
    if (retrace < params.pullback_min or retrace > params.pullback_max) and not preview:
        return None

    parts: dict[str, int] = {}
    reasons: list[str] = []
    if params.pullback_min <= retrace <= params.pullback_max:
        reasons.append(f"откат {retrace * 100:.0f}% импульса")

    o, h, l, c, body = _candle_info(bars[idx])
    prev = bars[idx - 1]
    po, ph, pl, pc, _ = _candle_info(prev)

    if bearish_engulfing((po, ph, pl, pc), (o, h, l, c)):
        parts["pattern"] = 25
        reasons.append("медвежье поглощение")
    elif bearish_pin_bar(o, h, l, c):
        parts["pattern"] = 20
        reasons.append("медвежий пин-бар")

    closes = [b[4] for b in bars]
    fast = ema_series(closes, params.ema_fast)
    slow = ema_series(closes, params.ema_slow)
    f, s = fast[idx], slow[idx]
    eps = max(close * 1e-5, 1e-8)
    if f is not None and abs(close - f) / close <= 0.004:
        parts["ema_touch"] = 15
        reasons.append(f"отбой от EMA{params.ema_fast}")
    if f is not None and s is not None and f < s:
        parts["trend"] = 12
        reasons.append("fast EMA ниже slow")
    if _inflection_down(fast, idx, eps):
        parts["ema_turn"] = 10
        reasons.append("наклон EMA вниз")

    if _sell_pressure(bars[: idx + 1], 3) >= 1.2:
        parts["volume"] = 10
        reasons.append("продажи на откате")

    score = max(0, min(100, sum(parts.values())))
    confirmed = parts.get("pattern", 0) >= 20 or (
        parts.get("ema_touch", 0) > 0 and retrace >= params.pullback_min
    )
    if preview:
        return _preview_candidate("short_continue", score, ctx, parts, reasons)
    if score < params.min_score or not confirmed:
        return None

    entry_low, entry_high = _zone_around(close)
    inv = ctx.impulse_high * 1.002
    return TvhCandidate(
        scenario="short_continue",
        score=score,
        entry_low=entry_low,
        entry_high=entry_high,
        invalidation=inv,
        entry_interval=ctx.entry_interval,
        reasons=reasons,
        score_parts=parts,
    )


def evaluate_tvh(
    bars: list[Bar],
    ctx: ImpulseContext,
    params: TvhParams | None = None,
    *,
    bar_idx: int | None = None,
    short_fade_only: bool = True,
) -> list[TvhCandidate]:
    """Оценка сценариев ТВХ на младшем TF (EMA по всей истории)."""
    p = params or TvhParams()
    if _resolve_bar_idx(bars, ctx, p, bar_idx) is None:
        return []
    out: list[TvhCandidate] = []
    if ctx.direction == "pump":
        fns = [_eval_fade_short_after_pump]
        if not short_fade_only:
            fns.append(_eval_continue_long_after_pump)
        for fn in fns:
            cand = fn(bars, ctx, p, bar_idx=bar_idx)
            if cand is not None:
                out.append(cand)
    elif not short_fade_only:
        for fn in (_eval_fade_long_after_dump, _eval_continue_short_after_dump):
            cand = fn(bars, ctx, p, bar_idx=bar_idx)
            if cand is not None:
                out.append(cand)
    return out


def filter_pump_short_fade(candidates: list[TvhCandidate]) -> list[TvhCandidate]:
    """Только шорт-фейд после pump (продолжение и dump-сценарии отключены)."""
    return [c for c in candidates if c.scenario == "short_fade"]


SCENARIO_LABELS: dict[TvhScenario, str] = {
    "short_fade": "📉 <b>Шорт</b> · разворот после pump",
    "long_continue": "📈 <b>Лонг</b> · продолжение pump",
    "long_fade": "📈 <b>Лонг</b> · разворот после dump",
    "short_continue": "📉 <b>Шорт</b> · продолжение dump",
}


def format_tvh_alert_lines(
    *,
    symbol: str,
    impulse_direction: ImpulseDirection,
    source_interval: str,
    impulse_pct: float,
    impulse_rvol: float,
    tvh: TvhCandidate,
) -> list[str]:
    icon = "🔥" if impulse_direction == "pump" else "🔻"
    pair = f"{symbol.upper()}USDT" if not symbol.upper().endswith("USDT") else symbol.upper()
    tf_src = interval_label(source_interval)
    tf_ent = interval_label(tvh.entry_interval)
    sign = "+" if impulse_pct > 0 else ""
    lines = [
        f"{icon} <code>{pair}</code> · ТВХ · {tf_ent}",
        SCENARIO_LABELS[tvh.scenario],
        f"Импульс: <b>{sign}{impulse_pct:.1f}%</b> · {tf_src} · RVOL <b>×{impulse_rvol:.1f}</b>",
        f"Качество ТВХ: <b>{tvh.score}</b>/100",
        (
            f"Зона: <b>{_fmt_price(tvh.entry_low)}</b> – <b>{_fmt_price(tvh.entry_high)}</b> · "
            f"стоп-ориентир: <b>{_fmt_price(tvh.invalidation)}</b>"
        ),
    ]
    if tvh.reasons:
        lines.append(" · ".join(tvh.reasons[:4]))
    return lines
