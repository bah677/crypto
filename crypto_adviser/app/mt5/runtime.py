"""Единая точка доступа к API MT5: нативный модуль MetaTrader5 или клиент mt5linux."""

from __future__ import annotations

from typing import Any

_handle: Any | None = None


def set_mt5_handle(obj: Any) -> None:
    global _handle
    _handle = obj


def get_mt5() -> Any:
    if _handle is None:
        raise RuntimeError("MT5 runtime не инициализирован (см. mt5_startup_if_configured в main)")
    return _handle


def clear_mt5_handle() -> None:
    global _handle
    _handle = None


def mt5_runtime_initialized() -> bool:
    return _handle is not None
