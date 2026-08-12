#!/usr/bin/env python3
"""Создать таблицы и начальные данные (без запуска Telegram-бота)."""

from __future__ import annotations

import asyncio
import sys


async def _main() -> None:
    from app.db.session import init_db

    await init_db()
    print("OK: таблицы созданы, seed выполнен (pump_scan_config, admins, subscribers).")


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
