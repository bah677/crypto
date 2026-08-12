from __future__ import annotations

from typing import Any


def ensure_symbol_selected(mt5: Any, symbol: str) -> bool:
    """
    Добавить символ в Market Watch. У официального пакета — symbol_info_select;
    у mt5linux часто только symbol_select.
    """
    if hasattr(mt5, "symbol_info_select"):
        return bool(mt5.symbol_info_select(symbol, True))
    if hasattr(mt5, "symbol_select"):
        return bool(mt5.symbol_select(symbol, True))
    return True
