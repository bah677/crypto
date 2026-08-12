"""Снимок зон EMA: сигнальный ТФ, МТФ (младший), СТФ (старший синт.)."""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.advisor.intervals import junior_interval_label, senior_interval_label
from app.advisor.mtf import (
    ema_zone,
    format_zone_line,
    junior_zone_at_signal,
    senior_zone_at_signal,
)
from app.advisor.tasks import AdvisorTask
from app.bybit.rest import BybitRest

MSK = ZoneInfo("Europe/Moscow")
_KLINE_LIMIT = 150
_REPORT_CHUNK_MAX = 3600


def _task_header(task: AdvisorTask) -> str:
    alias = task.alias.strip()
    tf = task.signal_interval_label()
    ema = f"EMA {task.ema_fast}/{task.ema_slow}"
    if alias:
        title = f"{alias} ({task.symbol}) · {tf} · {ema}"
    else:
        title = f"{task.symbol} · {tf} · {ema}"
    if not task.enabled:
        title += " · выкл"
    return title


def _zones_for_task(
    task: AdvisorTask,
    client: BybitRest,
    lower_bars_cache: dict[str, list[tuple[int, float, float, float, float]]],
) -> str | None:
    need = max(task.ema_fast, task.ema_slow) + 35
    kline_limit = min(_KLINE_LIMIT, max(80, need))
    bars = client.closed_ohlc_bars_with_ts(
        task.symbol, task.interval, limit=kline_limit
    )
    if not bars:
        return None

    idx = len(bars) - 1
    open_ms = bars[idx][0]
    closes = [b[4] for b in bars]
    main = ema_zone(closes, task.ema_fast, task.ema_slow)
    senior = senior_zone_at_signal(
        bars, idx, task.interval, task.ema_fast, task.ema_slow
    )
    junior = junior_zone_at_signal(
        task.interval,
        bars,
        idx,
        open_ms,
        task.ema_fast,
        task.ema_slow,
        client,
        task.symbol,
        lower_bars_cache,
    )

    mtf_lbl = junior_interval_label(task.interval)
    stf_lbl = senior_interval_label(task.interval)
    lines = [
        _task_header(task),
        format_zone_line(main),
        f"МТФ ({mtf_lbl}) – {format_zone_line(junior)}",
        f"СТФ ({stf_lbl}) – {format_zone_line(senior)}",
    ]
    return "\n".join(lines)


def build_zones_report_sync(tasks: list[AdvisorTask]) -> tuple[str, list[str]]:
    """
    Полный отчёт и список ошибок по заданиям.
    Задания обрабатываются по очереди (лимит Bybit).
    """
    if not tasks:
        return "Нет заданий в БД. Добавьте: <code>/task_add</code>", []

    now = datetime.now(tz=MSK).strftime("%H:%M MSK")
    blocks: list[str] = []
    errors: list[str] = []

    by_cat: dict[str, list[AdvisorTask]] = {}
    for t in tasks:
        cat = t.bybit_category or "linear"
        by_cat.setdefault(cat, []).append(t)

    for category, chunk in sorted(by_cat.items()):
        client = BybitRest(category=category)
        lower_cache: dict[str, list[tuple[int, float, float, float, float]]] = {}
        for task in chunk:
            try:
                block = _zones_for_task(task, client, lower_cache)
            except Exception as e:
                errors.append(f"{task.display_name}: {e}")
                continue
            if block is None:
                errors.append(f"{task.display_name}: нет свечей")
                continue
            blocks.append(block)
            time.sleep(0.18)

    if not blocks:
        return "Не удалось получить зоны ни по одному заданию.", errors

    body = f"<b>Зоны EMA</b> · {now}\n\n" + "\n\n".join(blocks)
    if errors:
        body += "\n\n⚠️ " + "\n".join(errors[:8])
        if len(errors) > 8:
            body += f"\n… ещё {len(errors) - 8}"
    return body, errors


def split_report_messages(text: str, max_len: int = _REPORT_CHUNK_MAX) -> list[str]:
    if len(text) <= max_len:
        return [text]
    blocks = text.split("\n\n")
    parts: list[str] = []
    buf = blocks[0]
    for block in blocks[1:]:
        candidate = f"{buf}\n\n{block}"
        if len(candidate) <= max_len:
            buf = candidate
        else:
            parts.append(buf)
            buf = block
    if buf.strip():
        parts.append(buf)
    return parts or [text]
