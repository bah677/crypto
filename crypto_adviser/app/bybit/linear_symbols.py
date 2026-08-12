"""Сопоставление монет с linear USDT-perp на Bybit."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pybit.exceptions import InvalidRequestError

from app.bybit.instruments import _http

log = logging.getLogger(__name__)

_NUM_PREFIX = re.compile(r"^(\d+)([A-Z0-9]+)$")


@dataclass(frozen=True)
class LinearInstrument:
    symbol: str
    base_coin: str
    funding_interval_hours: float
    symbol_type: str = ""
    price_limit_ratio_y: float = 0.2
    is_innovation: bool = False
    is_st: bool = False


def _parse_risk_y(item: dict) -> float:
    rp = item.get("riskParameters") or {}
    try:
        return float(rp.get("priceLimitRatioY") or 0.2)
    except (TypeError, ValueError):
        return 0.2


def _is_st_tag(price_limit_ratio_y: float) -> bool:
    """ST в API v5 нет явно; широкий priceLimitRatioY — эвристика Bybit ST."""
    return price_limit_ratio_y >= 0.4


def fetch_linear_usdt_instruments() -> dict[str, LinearInstrument]:
    """
    Все trading linear USDT-perp.
    Ключи: baseCoin (1000PEPE) и «короткое» имя (PEPE) для маппинга с CoinGecko.
    """
    http = _http()
    by_symbol: dict[str, LinearInstrument] = {}
    cursor: str | None = None

    while True:
        kwargs: dict = {"category": "linear", "limit": 1000}
        if cursor:
            kwargs["cursor"] = cursor
        try:
            r = http.get_instruments_info(**kwargs)
        except InvalidRequestError as e:
            raise RuntimeError(f"Bybit instruments-info: {e}") from e

        result = (r or {}).get("result") or {}
        for item in result.get("list") or []:
            if str(item.get("quoteCoin", "")).upper() != "USDT":
                continue
            if str(item.get("status", "")) != "Trading":
                continue
            symbol = str(item.get("symbol", "")).upper()
            base = str(item.get("baseCoin", "")).upper()
            if not symbol or not base:
                continue
            try:
                interval_min = float(item.get("fundingInterval") or 480)
            except (TypeError, ValueError):
                interval_min = 480.0
            sym_type = str(item.get("symbolType") or "").strip().lower()
            limit_y = _parse_risk_y(item)
            inst = LinearInstrument(
                symbol=symbol,
                base_coin=base,
                funding_interval_hours=interval_min / 60.0,
                symbol_type=sym_type,
                price_limit_ratio_y=limit_y,
                is_innovation=sym_type == "innovation",
                is_st=_is_st_tag(limit_y),
            )
            by_symbol[symbol] = inst
            _register_aliases(by_symbol, inst)

        cursor = result.get("nextPageCursor") or None
        if not cursor:
            break

    log.info("Bybit linear USDT: %s инструментов", len(by_symbol))
    return by_symbol


def _register_aliases(store: dict[str, LinearInstrument], inst: LinearInstrument) -> None:
    store[inst.base_coin] = inst
    store[inst.symbol] = inst
    m = _NUM_PREFIX.match(inst.base_coin)
    if m:
        short = m.group(2)
        if short not in store:
            store[short] = inst


def resolve_linear_symbol(
    coin_symbol: str,
    instruments: dict[str, LinearInstrument],
) -> LinearInstrument | None:
    """CoinGecko symbol (SOL, PEPE) → linear USDT-perp на Bybit."""
    key = coin_symbol.strip().upper()
    if not key:
        return None
    if key in instruments:
        return instruments[key]
    usdt = f"{key}USDT"
    if usdt in instruments:
        return instruments[usdt]
    return None
