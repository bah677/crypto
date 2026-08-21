"""Пул linear USDT: оборот 24h и метаданные инструментов."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.bybit.linear_symbols import LinearInstrument, fetch_linear_usdt_instruments
from app.bybit.rest import BybitRest

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinearTickerRow:
    symbol: str
    turnover_24h_usd: float
    volume_24h: float
    price_change_pct_24h: float | None


def fetch_linear_usdt_tickers() -> dict[str, LinearTickerRow]:
    """turnover24h Bybit linear USDT-perp (только USDT quote)."""
    out: dict[str, LinearTickerRow] = {}
    for raw in BybitRest(category="linear").get_linear_tickers():
        symbol = str(raw.get("symbol") or "").upper()
        if not symbol.endswith("USDT"):
            continue
        try:
            turnover = float(raw.get("turnover24h") or 0)
        except (TypeError, ValueError):
            turnover = 0.0
        try:
            volume = float(raw.get("volume24h") or 0)
        except (TypeError, ValueError):
            volume = 0.0
        pc_raw = raw.get("price24hPcnt")
        pc = float(pc_raw) * 100.0 if pc_raw not in (None, "") else None
        out[symbol] = LinearTickerRow(
            symbol=symbol,
            turnover_24h_usd=turnover,
            volume_24h=volume,
            price_change_pct_24h=pc,
        )
    log.info("Bybit linear tickers: %s USDT пар", len(out))
    return out


def build_bybit_turnover_candidates(
    *,
    min_turnover_usd: float,
    instruments: dict[str, LinearInstrument] | None = None,
    tickers: dict[str, LinearTickerRow] | None = None,
) -> list[tuple[LinearInstrument, LinearTickerRow, int]]:
    """
  Все linear USDT с turnover >= порога, отсортированы по обороту.
  Возвращает (instrument, ticker, rank) где rank 1 = макс. оборот.
    """
    inst_map = instruments or fetch_linear_usdt_instruments()
    tick_map = tickers or fetch_linear_usdt_tickers()
    rows: list[tuple[LinearInstrument, LinearTickerRow]] = []
    for symbol, ticker in tick_map.items():
        if ticker.turnover_24h_usd < min_turnover_usd:
            continue
        inst = inst_map.get(symbol)
        if inst is None or inst.symbol != symbol:
            continue
        rows.append((inst, ticker))
    rows.sort(key=lambda x: x[1].turnover_24h_usd, reverse=True)
    return [(inst, ticker, rank) for rank, (inst, ticker) in enumerate(rows, start=1)]
