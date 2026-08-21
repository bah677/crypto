"""Контекст рынка для pump-алертов: 24ч, день, funding."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace

from app.bybit.linear_symbols import fetch_linear_usdt_instruments
from app.bybit.priority import bybit_api_slot
from app.bybit.rest import BybitRest, _interval_to_ms
from app.market.binance_rank import binance_usdt_volume_rank
from app.market.funding_math import (
    funding_direction_ru,
    funding_rate_annual_percent,
    funding_rate_interval_percent,
)

log = logging.getLogger(__name__)

_MS_24H = 24 * 3600 * 1000


def _ohlcv_bars_at(
    client: BybitRest,
    symbol: str,
    interval: str,
    limit: int,
    *,
    as_of_ms: int | None,
) -> tuple[list[tuple], bool]:
    now_ms = as_of_ms if as_of_ms is not None else int(time.time() * 1000)
    raw = client.get_kline_ohlcv(symbol, interval, limit=limit, end_ms=as_of_ms)
    if not raw:
        return [], False
    raw.sort(key=lambda x: x[0])
    step = _interval_to_ms(interval)
    closed = [bar for bar in raw if bar[0] + step <= now_ms]
    in_progress = [bar for bar in raw if bar[0] <= now_ms < bar[0] + step]
    bars = list(closed)
    has_forming = False
    if in_progress:
        bars.append(in_progress[-1])
        has_forming = True
    return bars, has_forming


@dataclass(frozen=True)
class AlertMarketContext:
    change_24h_pct: float | None = None
    change_day_pct: float | None = None
    funding_interval_pct: float | None = None
    funding_annual_pct: float | None = None
    funding_interval_hours: float | None = None
    binance_volume_rank: int | None = None

    @property
    def funding_direction(self) -> str:
        if self.funding_annual_pct is None:
            return "—"
        return funding_direction_ru(self.funding_annual_pct)


def _fmt_pct(value: float, *, decimals: int = 1) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{decimals}f}%"


def _fmt_interval_hours(h: float) -> str:
    if abs(h - round(h)) < 0.01:
        ih = int(round(h))
        return f"{ih}ч" if ih != 1 else "1ч"
    return f"{h:.1f}ч"


def _funding_extreme(annual_pct: float | None) -> bool:
    if annual_pct is None:
        return False
    return abs(annual_pct) >= 1000.0


def format_market_context_lines(ctx: AlertMarketContext | None) -> list[str]:
    if ctx is None:
        return []
    lines: list[str] = []
    price_parts: list[str] = []
    if ctx.change_24h_pct is not None:
        price_parts.append(f"24ч: <b>{_fmt_pct(ctx.change_24h_pct)}</b>")
    if ctx.change_day_pct is not None:
        price_parts.append(f"день: <b>{_fmt_pct(ctx.change_day_pct)}</b>")
    if price_parts:
        lines.append(" · ".join(price_parts))
    if ctx.binance_volume_rank is not None:
        lines.append(f"Binance 24ч: <b>#{ctx.binance_volume_rank}</b> по объёму")
    else:
        # Показываем строку всегда, чтобы формат алерта был стабильным
        lines.append("Binance 24ч: <b>—</b> по объёму")
    show_funding = (
        ctx.funding_annual_pct is not None
        and ctx.funding_interval_pct is not None
        and _funding_extreme(ctx.funding_annual_pct)
    )
    if show_funding:
        ih = ctx.funding_interval_hours or 8.0
        lines.append(
            f"❗️ Фандинг: {ctx.funding_direction} · "
            f"<b>{_fmt_pct(ctx.funding_annual_pct, decimals=0)}</b> год · "
            f"<b>{_fmt_pct(ctx.funding_interval_pct, decimals=2)}</b>/{_fmt_interval_hours(ih)}"
        )
    return lines


def _funding_from_ticker(
    ticker: dict,
    *,
    default_interval_h: float | None,
) -> tuple[float, float, float] | None:
    raw_rate = ticker.get("fundingRate")
    if raw_rate is None or raw_rate == "":
        return None
    try:
        interval_h = float(ticker.get("fundingIntervalHour") or 0)
    except (TypeError, ValueError):
        interval_h = 0.0
    if interval_h <= 0:
        interval_h = default_interval_h or 8.0
    interval_pct = funding_rate_interval_percent(raw_rate)
    annual_pct = funding_rate_annual_percent(raw_rate, interval_h)
    return interval_pct, annual_pct, interval_h


def _change_24h_from_ticker(ticker: dict) -> float | None:
    raw = ticker.get("price24hPcnt")
    if raw in (None, ""):
        return None
    try:
        return float(raw) * 100.0
    except (TypeError, ValueError):
        return None


def _change_24h_historical(client: BybitRest, symbol: str, as_of_ms: int) -> float | None:
    bars = client.get_kline_ohlcv(symbol, "60", limit=30, end_ms=as_of_ms)
    if not bars:
        return None
    bars.sort(key=lambda x: x[0])
    end_close = bars[-1][4]
    target = as_of_ms - _MS_24H
    start_close = None
    for ts, _, _, _, c, _ in bars:
        if ts <= target:
            start_close = c
    if start_close is None and bars:
        start_close = bars[0][4]
    if start_close is None or start_close <= 0:
        return None
    return (end_close - start_close) / start_close * 100.0


def _change_day_pct(
    client: BybitRest,
    symbol: str,
    *,
    as_of_ms: int | None,
    last_price: float | None,
) -> float | None:
    bars, _forming = _ohlcv_bars_at(
        client, symbol, "D", limit=3, as_of_ms=as_of_ms
    )
    if not bars:
        return None
    _, o, _, _, c, _ = bars[-1]
    px = last_price if as_of_ms is None and last_price is not None else c
    if o <= 0:
        return None
    return (px - o) / o * 100.0


def _funding_historical(
    client: BybitRest,
    symbol: str,
    as_of_ms: int,
    *,
    default_interval_h: float | None,
) -> tuple[float, float, float] | None:
    try:
        with bybit_api_slot():
            r = client._http.get_funding_rate_history(
                category="linear",
                symbol=symbol,
                endTime=as_of_ms,
                limit=1,
            )
    except Exception:
        log.debug("Funding history skip %s", symbol, exc_info=True)
        return None
    lst = (r or {}).get("result", {}).get("list") or []
    if not lst:
        return None
    row = lst[0]
    raw_rate = row.get("fundingRate")
    if raw_rate is None or raw_rate == "":
        return None
    interval_h = default_interval_h or 8.0
    interval_pct = funding_rate_interval_percent(raw_rate)
    annual_pct = funding_rate_annual_percent(raw_rate, interval_h)
    return interval_pct, annual_pct, interval_h


def fetch_market_context(
    client: BybitRest,
    symbol: str,
    *,
    ticker: dict | None = None,
    funding_interval_h: float | None = None,
    as_of_ms: int | None = None,
) -> AlertMarketContext:
    sym = symbol.upper()
    change_24h: float | None = None
    change_day: float | None = None
    funding: tuple[float, float, float] | None = None
    last_price: float | None = None

    if as_of_ms is None and ticker is not None:
        change_24h = _change_24h_from_ticker(ticker)
        try:
            last_price = float(ticker.get("lastPrice") or 0) or None
        except (TypeError, ValueError):
            last_price = None
        funding = _funding_from_ticker(ticker, default_interval_h=funding_interval_h)
    elif as_of_ms is not None:
        change_24h = _change_24h_historical(client, sym, as_of_ms)
        funding = _funding_historical(
            client, sym, as_of_ms, default_interval_h=funding_interval_h
        )

    change_day = _change_day_pct(
        client, sym, as_of_ms=as_of_ms, last_price=last_price
    )

    binance_rank: int | None = None
    if as_of_ms is None:
        try:
            binance_rank = binance_usdt_volume_rank(sym)
        except Exception:
            log.debug("Binance rank skip %s", sym, exc_info=True)

    base = dict(
        change_24h_pct=change_24h,
        change_day_pct=change_day,
        binance_volume_rank=binance_rank,
    )

    if funding is None:
        return AlertMarketContext(**base)
    interval_pct, annual_pct, interval_h = funding
    return AlertMarketContext(
        **base,
        funding_interval_pct=interval_pct,
        funding_annual_pct=annual_pct,
        funding_interval_hours=interval_h,
    )


def enrich_hits_market_context(
    hits: list,
    *,
    as_of_ms: int | None = None,
) -> list:
    if not hits:
        return hits

    client = BybitRest(category="linear")
    symbols = sorted({h.symbol.upper() for h in hits})

    ticker_map: dict[str, dict] = {}
    if as_of_ms is None:
        ticker_map = {t["symbol"]: t for t in client.get_linear_tickers()}

    inst_map = fetch_linear_usdt_instruments()
    ctx_cache: dict[str, AlertMarketContext] = {}

    for sym in symbols:
        inst = inst_map.get(sym)
        interval_h = inst.funding_interval_hours if inst else None
        try:
            ctx_cache[sym] = fetch_market_context(
                client,
                sym,
                ticker=ticker_map.get(sym),
                funding_interval_h=interval_h,
                as_of_ms=as_of_ms,
            )
        except Exception:
            log.debug("Market context skip %s", sym, exc_info=True)
            ctx_cache[sym] = AlertMarketContext()

    return [replace(h, market=ctx_cache.get(h.symbol.upper())) for h in hits]
