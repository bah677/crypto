"""Поиск инструмента Bybit v5 по категориям рынка."""

from __future__ import annotations

import logging

from pybit.exceptions import InvalidRequestError
from pybit.unified_trading import HTTP

from app.config import get_settings

log = logging.getLogger(__name__)

# spot + USDT-бессрочные (linear). inverse — отдельно, при необходимости.
MARKET_CATEGORIES = ("spot", "linear")

_CATEGORY_LABEL = {
    "spot": "спот",
    "linear": "бессрочные USDT-фьючерсы",
}


def market_label(category: str) -> str:
    """Человекочитаемое название рынка Bybit."""
    c = (category or "").strip().lower()
    return {
        "spot": "Спот",
        "linear": "Бессрочный фьючерс",
        "inverse": "Inverse-фьючерс",
    }.get(c, category)


def _http() -> HTTP:
    s = get_settings()
    return HTTP(
        testnet=s.bybit_network.lower() == "testnet",
        api_key=s.bybit_api_key,
        api_secret=s.bybit_api_secret,
    )


def _symbol_in_category(http: HTTP, category: str, symbol: str) -> bool:
    """Bybit на несуществующий symbol часто отвечает ErrCode 10001 (exception), не пустым list."""
    try:
        r = http.get_instruments_info(category=category, symbol=symbol.upper())
    except InvalidRequestError as e:
        log.debug("instruments-info %s %s: %s", category, symbol, e)
        return False
    lst = (r or {}).get("result", {}).get("list") or []
    return bool(lst)


def find_symbol_markets(symbol: str) -> list[str]:
    """
    Ищет символ на споте и бессрочных USDT-фьючерсах.
    Возвращает список category, где тикер найден (spot, linear).
    """
    sym = symbol.strip().upper()
    http = _http()
    found: list[str] = []
    for cat in MARKET_CATEGORIES:
        if _symbol_in_category(http, cat, sym):
            found.append(cat)
    return found


def resolve_symbol_category(symbol: str) -> str:
    """
    Однозначный рынок: если тикер только на одном — его;
    если на нескольких — BYBIT_CATEGORY из .env, иначе первый найденный.
    """
    markets = find_symbol_markets(symbol)
    if not markets:
        sym = symbol.strip().upper()
        tried = ", ".join(_CATEGORY_LABEL.get(c, c) for c in MARKET_CATEGORIES)
        raise RuntimeError(f"Инструмент {sym} не найден на Bybit ({tried})")
    if len(markets) == 1:
        return markets[0]
    preferred = (get_settings().bybit_category or "linear").strip().lower()
    if preferred in markets:
        return preferred
    return markets[0]


def verify_bybit_symbol(symbol: str) -> str:
    """Проверка тикера; возвращает category (без выбора пользователя)."""
    return resolve_symbol_category(symbol)
