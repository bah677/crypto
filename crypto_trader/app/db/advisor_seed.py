"""Однократный импорт заданий из ADVISOR_TASKS и курсоров из advisor_state.json."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.advisor.tasks import parse_advisor_tasks
from app.bybit.instruments import resolve_symbol_category
from app.config import ENV_FILE
from app.db.models import AdvisorTaskRow
from app.repository.advisor_tasks import (
    add_advisor_task,
    count_advisor_tasks,
    fetch_all_advisor_tasks,
)

log = logging.getLogger(__name__)

_STATE_PATH = Path(__file__).resolve().parents[2] / "data" / "advisor_state.json"


def _load_json_cursors() -> dict[str, int]:
    if not _STATE_PATH.is_file():
        return {}
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        raw = data.get("cursors") or {}
        return {str(k): int(v) for k, v in raw.items()}
    except Exception:
        log.warning("Не удалось прочитать %s при импорте", _STATE_PATH)
        return {}


async def seed_advisor_tasks_if_empty(session: AsyncSession) -> int:
    """
    Если таблица advisor_tasks пуста — импорт из env ADVISOR_TASKS (legacy).
    Курсоры из data/advisor_state.json подставляются по ключу задания.
    """
    n = await count_advisor_tasks(session)
    if n > 0:
        return 0

    raw = (dotenv_values(ENV_FILE).get("ADVISOR_TASKS") or "").strip()
    default_hours = dotenv_values(ENV_FILE).get("ADVISOR_TRADING_HOURS") or ""
    if not raw:
        return 0

    try:
        parsed = parse_advisor_tasks(raw, default_hours)
    except ValueError as e:
        log.error("ADVISOR_TASKS: ошибка разбора при импорте: %s", e)
        return 0

    cursors = _load_json_cursors()
    imported = 0
    for task in parsed:
        cursor = cursors.get(task.key)
        try:
            cat = await asyncio.to_thread(resolve_symbol_category, task.symbol)
        except RuntimeError as e:
            log.error("ADVISOR_TASKS: %s — %s", task.symbol, e)
            continue
        await add_advisor_task(
            session,
            symbol=task.symbol,
            ema_fast=task.ema_fast,
            ema_slow=task.ema_slow,
            kline_interval=task.interval,
            bybit_category=cat,
            trading_hours=task.trading_hours,
            enabled=True,
            last_evaluated_bar_open_ms=cursor,
        )
        imported += 1

    if imported:
        log.info(
            "Импортировано %s заданий советчика из ADVISOR_TASKS → advisor_tasks (БД)",
            imported,
        )
    return imported


async def migrate_cursors_from_json(session: AsyncSession) -> None:
    """Перенос курсоров из JSON в строки БД (если в JSON есть, в БД ещё нет)."""
    cursors = _load_json_cursors()
    if not cursors:
        return
    res = await session.execute(select(AdvisorTaskRow))
    rows = list(res.scalars().all())
    changed = False
    for row in rows:
        if row.last_evaluated_bar_open_ms is not None:
            continue
        open_ms = cursors.get(row.task_key)
        if open_ms is not None:
            row.last_evaluated_bar_open_ms = open_ms
            changed = True
    if changed:
        await session.commit()
        log.info("Курсоры советчика перенесены из advisor_state.json в БД")


async def sync_advisor_task_categories(session: AsyncSession) -> None:
    """Если сохранённый рынок недоступен — подставить единственный найденный."""
    from app.bybit.instruments import find_symbol_markets

    rows = await fetch_all_advisor_tasks(session)
    if not rows:
        return
    changed = False
    for row in rows:
        try:
            markets = await asyncio.to_thread(find_symbol_markets, row.symbol)
        except Exception:
            continue
        if not markets:
            continue
        if row.bybit_category in markets:
            continue
        if len(markets) == 1:
            row.bybit_category = markets[0]
            changed = True
    if changed:
        await session.commit()
        log.info("Исправлены недоступные рынки (bybit_category) для заданий советчика")
