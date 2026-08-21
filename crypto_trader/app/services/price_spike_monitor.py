"""Мониторинг скачков на linear: полный ход 1m-свечи vs средний за час."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.bybit.rest import BybitRest
from app.config import get_settings
from app.indicators.volatility import candle_range_pct

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

_KLINE_INTERVAL_1M = "1"
_KLINE_LIMIT = 65
_MIN_CLOSED_BARS = 61
_MIN_BASELINE_BARS = 30

_last_alert_mono: dict[str, float] = {}


@dataclass(frozen=True)
class SpikeHit:
    symbol: str
    alias: str
    direction: str
    range_usd: float
    range_usd_label: str
    range_1m_pct: float
    ratio: float


def _decimals_from_tick(tick: Decimal) -> int:
    s = format(tick, "f").rstrip("0").rstrip(".")
    if "." not in s:
        return 0
    return len(s.split(".", 1)[1])


def _format_range_usd(high: float, low: float, tick_size: Decimal) -> tuple[float, str]:
    """Linear USDT: high−low уже в USDT (≈ $ за единицу цены контракта)."""
    amount = high - low
    decimals = _decimals_from_tick(tick_size)
    label = f"${amount:,.{decimals}f}".replace(",", " ")
    return amount, label


def _detect_spike_from_1m(
    bars: list[tuple[int, float, float, float, float]],
    ratio_threshold: float,
) -> tuple[float, float, float] | None:
    """
    Последняя закрытая 1m-свеча: (high−low)/close в %.
    Фон — среднее такого же размаха за 60 предыдущих минут.
    """
    if len(bars) < _MIN_CLOSED_BARS:
        return None

    window = bars[-_MIN_CLOSED_BARS:]
    ranges = [candle_range_pct(h, l, c) for _, _, h, l, c in window]
    last_range = ranges[-1]
    baseline = ranges[:-1]
    if len(baseline) < _MIN_BASELINE_BARS:
        return None

    avg_range = sum(baseline) / len(baseline)
    if avg_range <= 1e-9:
        return None

    ratio = last_range / avg_range
    if ratio < ratio_threshold:
        return None
    return last_range, avg_range, ratio


def _upper_wick(open_px: float, high: float, close_px: float) -> float:
    return high - max(open_px, close_px)


def _lower_wick(open_px: float, low: float, close_px: float) -> float:
    return min(open_px, close_px) - low


def _spike_direction(
    o: float,
    h: float,
    l: float,
    c: float,
    po: float,
    ph: float,
    pl: float,
    pc: float,
) -> str:
    """
    Куда ушёл импульс на аномальной 1m от её open:
    вверх = high − open, вниз = open − low (тело и тень в одну сторону).
    При равенстве — пробой high/low прошлой свечи.
    """
    rise = h - o
    drop = o - l
    if drop > rise:
        return "🔴 вниз"
    if rise > drop:
        return "🟢 вверх"

    up_ext = h - ph
    down_ext = pl - l
    if down_ext > up_ext and down_ext > 0:
        return "🔴 вниз"
    if up_ext > down_ext and up_ext > 0:
        return "🟢 вверх"
    if up_ext > 0 and down_ext > 0:
        return "⚪️ обе стороны"

    upper_now = _upper_wick(o, h, c)
    lower_now = _lower_wick(o, l, c)
    if lower_now > upper_now:
        return "🔴 вниз"
    if upper_now > lower_now:
        return "🟢 вверх"
    return "⚪️ флэт"


def format_spike_message(hit: SpikeHit) -> str:
    now = datetime.now(tz=MSK).strftime("%H:%M MSK")
    return (
        f"⚡ Скачок цены · {hit.alias} {hit.direction} · {now}\n"
        f"Ход {hit.range_usd_label}"
    )


def _cooldown_ok(symbol: str, cooldown_min: int) -> bool:
    sym = symbol.upper()
    last = _last_alert_mono.get(sym, 0.0)
    return (time.monotonic() - last) >= cooldown_min * 60


def _mark_alerted(symbol: str) -> None:
    _last_alert_mono[symbol.upper()] = time.monotonic()


def _collect_symbols_sync(
    watch_symbols: list[tuple[str, str]],
) -> dict[str, str]:
    """symbol -> подпись в алерте (алиас или тикер)."""
    out: dict[str, str] = {}
    client = BybitRest(category="linear")
    try:
        for sym in client.list_open_linear_symbols():
            out[sym.upper()] = sym.upper()
    except Exception:
        log.exception("Price spike: не удалось загрузить открытые позиции")
    for sym, alias in watch_symbols:
        key = sym.upper()
        if alias.strip():
            out[key] = alias.strip()
        else:
            out.setdefault(key, key)
    return out


def _tick_symbol_sync(
    symbol: str,
    alias: str,
    ratio_threshold: float,
    cooldown_min: int,
) -> SpikeHit | None:
    client = BybitRest(category="linear")
    bars = client.closed_ohlc_bars_with_ts(
        symbol, _KLINE_INTERVAL_1M, limit=_KLINE_LIMIT
    )
    if not bars:
        log.debug("Price spike: нет 1m свечей %s", symbol)
        return None

    if not _cooldown_ok(symbol, cooldown_min):
        return None

    detected = _detect_spike_from_1m(bars, ratio_threshold)
    if detected is None:
        return None

    range_1m, _avg_range, ratio = detected
    _, o, h, l, c = bars[-1]
    _, po, ph, pl, pc = bars[-2]
    tick_size, _ = client.instrument_filters(symbol)
    range_usd, range_label = _format_range_usd(h, l, tick_size)
    return SpikeHit(
        symbol=symbol.upper(),
        alias=alias,
        direction=_spike_direction(o, h, l, c, po, ph, pl, pc),
        range_usd=range_usd,
        range_usd_label=range_label,
        range_1m_pct=range_1m,
        ratio=ratio,
    )


def _run_price_spike_sync(
    watch: list[tuple[str, str]],
    threshold: float,
    cooldown: int,
) -> list[tuple[str, SpikeHit, str]]:
    from app.bybit.priority import background_request_scope

    out: list[tuple[str, SpikeHit, str]] = []
    with background_request_scope():
        symbols_map = _collect_symbols_sync(watch)
        if not symbols_map:
            return out
        for sym, alias in sorted(symbols_map.items()):
            try:
                hit = _tick_symbol_sync(sym, alias, threshold, cooldown)
            except Exception:
                log.exception("Price spike: сбой %s", sym)
                continue
            if hit is None:
                continue
            out.append((sym, hit, format_spike_message(hit)))
    return out


async def run_price_spike_tick() -> None:
    from app.bybit.priority import end_background_tick, try_begin_background_tick
    from app.db.session import session_scope
    from app.repository.price_watch import fetch_enabled_price_watch
    from app.services.admin_notify import notify_price_spike_channel

    s = get_settings()
    from app.services.alert_toggles import price_spike_reports_active

    if not await price_spike_reports_active():
        return

    if not await asyncio.to_thread(try_begin_background_tick, "price_spike"):
        return

    try:
        async with session_scope() as session:
            rows = await fetch_enabled_price_watch(session)
        watch = [(r.symbol, r.alias or "") for r in rows]
        hits = await asyncio.to_thread(
            _run_price_spike_sync,
            watch,
            s.price_spike_ratio,
            s.price_spike_alert_cooldown_min,
        )
    finally:
        await asyncio.to_thread(end_background_tick)

    for sym, hit, msg in hits:
        try:
            await notify_price_spike_channel(msg)
            _mark_alerted(sym)
            log.info(
                "Price spike: %s %s · %s (%.3f%%, %.1f×)",
                sym,
                hit.direction,
                hit.range_usd_label,
                hit.range_1m_pct,
                hit.ratio,
            )
        except Exception:
            log.exception("Price spike: не отправили алерт %s", sym)
