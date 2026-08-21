"""Ссылки на торговлю Bybit."""

from __future__ import annotations


def bybit_usdt_perp_trade_url(symbol: str) -> str:
    """Linear USDT-perp: https://www.bybit.com/trade/usdt/SYMBOL"""
    return f"https://www.bybit.com/trade/usdt/{symbol.upper().strip()}"
