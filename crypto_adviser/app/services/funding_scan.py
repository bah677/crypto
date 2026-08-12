"""Скан funding: топ альтов по cap → Bybit linear → годовые % > порога."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.bybit.linear_symbols import fetch_linear_usdt_instruments, resolve_linear_symbol
from app.bybit.rest import BybitRest
from app.config import get_settings
from app.market.coingecko import CoinMarketRow, fetch_top_altcoins
from app.market.funding_math import (
    funding_direction_ru,
    funding_rate_annual_percent,
    funding_rate_interval_percent,
)

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


@dataclass(frozen=True)
class FundingHit:
    symbol: str
    coin_name: str
    market_cap_rank: int
    interval_hours: float
    interval_pct: float
    annual_pct: float

    @property
    def direction(self) -> str:
        return funding_direction_ru(self.annual_pct)


def _scan_sync(*, top_n: int, threshold_annual: float) -> list[FundingHit]:
    coins = fetch_top_altcoins(limit=top_n)
    instruments = fetch_linear_usdt_instruments()
    tickers = {t["symbol"]: t for t in BybitRest(category="linear").get_linear_tickers()}

    hits: list[FundingHit] = []
    matched = 0
    for coin in coins:
        inst = resolve_linear_symbol(coin.symbol, instruments)
        if inst is None:
            continue
        matched += 1
        ticker = tickers.get(inst.symbol)
        if not ticker:
            continue
        raw_rate = ticker.get("fundingRate")
        if raw_rate is None or raw_rate == "":
            continue
        try:
            interval_h = float(ticker.get("fundingIntervalHour") or inst.funding_interval_hours)
        except (TypeError, ValueError):
            interval_h = inst.funding_interval_hours
        if interval_h <= 0:
            continue

        annual = funding_rate_annual_percent(raw_rate, interval_h)
        if abs(annual) <= threshold_annual:
            continue

        interval_pct = funding_rate_interval_percent(raw_rate)
        hits.append(
            FundingHit(
                symbol=inst.symbol,
                coin_name=coin.name,
                market_cap_rank=coin.market_cap_rank,
                interval_hours=interval_h,
                interval_pct=interval_pct,
                annual_pct=annual,
            )
        )

    hits.sort(key=lambda h: abs(h.annual_pct), reverse=True)
    log.info(
        "Funding scan: альтов=%s, на Bybit=%s, выше порога=%s",
        len(coins),
        matched,
        len(hits),
    )
    return hits


def _fmt_pct(value: float, *, decimals: int = 2) -> str:
    return f"{value:+.{decimals}f}%"


def _fmt_interval_hours(h: float) -> str:
    if abs(h - round(h)) < 0.01:
        ih = int(round(h))
        return f"{ih}ч" if ih != 1 else "1ч"
    return f"{h:.1f}ч"


def format_funding_scan_message(
    hits: list[FundingHit],
    *,
    threshold_annual: float,
    top_n: int,
    scanned_at: datetime | None = None,
) -> str:
    ts = (scanned_at or datetime.now(tz=MSK)).strftime("%Y-%m-%d %H:%M MSK")
    if not hits:
        return (
            f"⚡ Funding · {ts}\n"
            f"|годовые| ≤ {threshold_annual:.0f}% · топ-{top_n} альтов — ничего не найдено"
        )

    lines = [
        f"⚡ Funding · {ts}",
        f"|годовые| > {threshold_annual:.0f}% · топ-{top_n} альтов",
        "",
    ]
    for h in hits:
        emoji = "🔴" if h.annual_pct < 0 else "🟢"
        lines.append(
            f"{emoji} {h.symbol} · {_fmt_pct(h.annual_pct)} год · "
            f"{_fmt_pct(h.interval_pct)}/{_fmt_interval_hours(h.interval_hours)} · "
            f"{h.direction}"
        )
    return "\n".join(lines)


async def run_funding_scan(
    *,
    notify: bool = True,
    notify_if_empty: bool = False,
    force: bool = False,
    top_n: int | None = None,
    threshold_annual: float | None = None,
) -> list[FundingHit]:
    settings = get_settings()
    if not force:
        from app.services.alert_toggles import funding_reports_active

        if not await funding_reports_active():
            log.debug("Funding scan: выкл (.env или /alerts)")
            return []

    threshold = (
        threshold_annual
        if threshold_annual is not None
        else settings.funding_annual_threshold
    )
    top_n_val = top_n if top_n is not None else settings.funding_top_n

    try:
        hits = await asyncio.to_thread(
            _scan_sync,
            top_n=top_n_val,
            threshold_annual=threshold,
        )
    except Exception:
        log.exception("Funding scan: сбой")
        if notify:
            from app.services.admin_notify import notify_superadmin

            await notify_superadmin("⚠️ Funding scan: ошибка — см. err.log")
        raise

    if notify and (hits or notify_if_empty):
        from app.services.admin_notify import notify_funding_channel

        msg = format_funding_scan_message(
            hits,
            threshold_annual=threshold,
            top_n=top_n_val,
        )
        await notify_funding_channel(msg, parse_mode=None)

    return hits
