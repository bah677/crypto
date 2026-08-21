"""Символы для мониторинга: открытые linear-позиции + ручной watch."""

from __future__ import annotations

import logging

from app.bybit.rest import BybitRest

log = logging.getLogger(__name__)


def collect_monitored_symbols_sync(
    watch_symbols: list[tuple[str, str]],
) -> dict[str, str]:
    """symbol -> подпись (алиас watch или тикер)."""
    out: dict[str, str] = {}
    client = BybitRest(category="linear")
    try:
        for sym in client.list_open_linear_symbols():
            out[sym.upper()] = sym.upper()
    except Exception:
        log.exception("Не удалось загрузить открытые linear-позиции")
    for sym, alias in watch_symbols:
        key = sym.upper()
        if alias.strip():
            out[key] = alias.strip()
        else:
            out.setdefault(key, key)
    return out
