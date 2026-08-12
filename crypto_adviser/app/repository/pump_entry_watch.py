"""Repository: entry watch + suggestions."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PumpEntryWatchRow, PumpEntryWatchSuggestionRow


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


async def upsert_entry_watch_suggestion(
    session: AsyncSession,
    *,
    symbol: str,
    source_chat_id: int,
    source_message_id: int,
    impulse_price: float,
    impulse_interval: str,
    entry_timing: str,
    watch_if_early: bool,
    watch_plan: dict[str, Any],
    alert_text: str,
    analysis_excerpt: str,
    expires_at: datetime,
) -> PumpEntryWatchSuggestionRow:
    r = await session.execute(
        select(PumpEntryWatchSuggestionRow).where(
            PumpEntryWatchSuggestionRow.source_chat_id == source_chat_id,
            PumpEntryWatchSuggestionRow.source_message_id == source_message_id,
        )
    )
    row = r.scalar_one_or_none()
    if row is None:
        row = PumpEntryWatchSuggestionRow(
            symbol=symbol.upper(),
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            impulse_price=float(impulse_price),
            impulse_interval=str(impulse_interval),
            entry_timing=entry_timing,
            watch_if_early=watch_if_early,
            watch_plan_json=_dumps(watch_plan),
            alert_text=(alert_text or "")[:4000],
            analysis_excerpt=(analysis_excerpt or "")[:4000],
            expires_at=expires_at,
        )
        session.add(row)
    else:
        row.symbol = symbol.upper()
        row.impulse_price = float(impulse_price)
        row.impulse_interval = str(impulse_interval)
        row.entry_timing = entry_timing
        row.watch_if_early = watch_if_early
        row.watch_plan_json = _dumps(watch_plan)
        row.alert_text = (alert_text or "")[:4000]
        row.analysis_excerpt = (analysis_excerpt or "")[:4000]
        row.expires_at = expires_at
    await session.flush()
    return row


async def get_suggestion_for_message(
    session: AsyncSession,
    *,
    source_chat_id: int,
    source_message_id: int,
    now: datetime,
) -> PumpEntryWatchSuggestionRow | None:
    r = await session.execute(
        select(PumpEntryWatchSuggestionRow).where(
            PumpEntryWatchSuggestionRow.source_chat_id == source_chat_id,
            PumpEntryWatchSuggestionRow.source_message_id == source_message_id,
            PumpEntryWatchSuggestionRow.expires_at > now,
        )
    )
    return r.scalar_one_or_none()


async def get_latest_suggestion_for_symbol(
    session: AsyncSession,
    *,
    symbol: str,
    now: datetime,
) -> PumpEntryWatchSuggestionRow | None:
    r = await session.execute(
        select(PumpEntryWatchSuggestionRow)
        .where(
            PumpEntryWatchSuggestionRow.symbol == symbol.upper(),
            PumpEntryWatchSuggestionRow.expires_at > now,
        )
        .order_by(PumpEntryWatchSuggestionRow.id.desc())
        .limit(1)
    )
    return r.scalar_one_or_none()


async def create_entry_watch(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    symbol: str,
    source_chat_id: int | None,
    source_message_id: int | None,
    impulse_price: float,
    impulse_interval: str,
    alert_text: str,
    watch_plan: dict[str, Any],
    baseline_metrics: dict[str, Any],
    expires_at: datetime,
    initial_analysis: str = "",
    initial_entry_timing: str = "unknown",
) -> PumpEntryWatchRow:
    phase = str(baseline_metrics.get("squeeze_phase") or "squeeze_building")
    row = PumpEntryWatchRow(
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        symbol=symbol.upper(),
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        impulse_price=float(impulse_price),
        impulse_interval=str(impulse_interval),
        alert_text=(alert_text or "")[:4000],
        initial_analysis=(initial_analysis or "")[:6000],
        initial_entry_timing=(initial_entry_timing or "unknown")[:16],
        analysis_history_json="[]",
        watch_plan_json=_dumps(watch_plan),
        baseline_metrics_json=_dumps(baseline_metrics),
        last_metrics_json=_dumps(baseline_metrics),
        high_watermark_price=(
            float(baseline_metrics["price"])
            if baseline_metrics.get("price") is not None
            else float(impulse_price)
        ),
        current_phase=phase,
        phase_history_json=_dumps([{"at": datetime.utcnow().isoformat(), "phase": phase, "kind": "created"}]),
        status="active",
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    return row


async def count_active_user_watches(session: AsyncSession, telegram_user_id: int) -> int:
    r = await session.execute(
        select(PumpEntryWatchRow).where(
            PumpEntryWatchRow.telegram_user_id == telegram_user_id,
            PumpEntryWatchRow.status == "active",
        )
    )
    return len(list(r.scalars().all()))


async def find_active_user_symbol_watch(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    symbol: str,
) -> PumpEntryWatchRow | None:
    r = await session.execute(
        select(PumpEntryWatchRow)
        .where(
            PumpEntryWatchRow.telegram_user_id == telegram_user_id,
            PumpEntryWatchRow.symbol == symbol.upper(),
            PumpEntryWatchRow.status == "active",
        )
        .order_by(PumpEntryWatchRow.id.asc())
        .limit(1)
    )
    return r.scalar_one_or_none()


async def find_active_watch_for_source(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    source_chat_id: int,
    source_message_id: int,
) -> PumpEntryWatchRow | None:
    r = await session.execute(
        select(PumpEntryWatchRow)
        .where(
            PumpEntryWatchRow.telegram_user_id == telegram_user_id,
            PumpEntryWatchRow.source_chat_id == source_chat_id,
            PumpEntryWatchRow.source_message_id == source_message_id,
            PumpEntryWatchRow.status == "active",
        )
        .order_by(PumpEntryWatchRow.id.asc())
        .limit(1)
    )
    return r.scalar_one_or_none()


async def cancel_duplicate_active_watches(
    session: AsyncSession,
    *,
    keep_id: int,
    telegram_user_id: int,
    symbol: str,
    now: datetime,
) -> int:
    """Снимает лишние active watch по user+symbol, оставляет keep_id."""
    r = await session.execute(
        select(PumpEntryWatchRow).where(
            PumpEntryWatchRow.telegram_user_id == telegram_user_id,
            PumpEntryWatchRow.symbol == symbol.upper(),
            PumpEntryWatchRow.status == "active",
            PumpEntryWatchRow.id != keep_id,
        )
    )
    extras = list(r.scalars().all())
    for row in extras:
        row.status = "cancelled"
        row.completion_note = f"Дубликат #{keep_id}"
        row.completed_at = now
    await session.flush()
    return len(extras)


async def fetch_active_entry_watches(session: AsyncSession) -> list[PumpEntryWatchRow]:
    r = await session.execute(
        select(PumpEntryWatchRow)
        .where(PumpEntryWatchRow.status == "active")
        .order_by(PumpEntryWatchRow.id)
    )
    return list(r.scalars().all())


async def fetch_user_entry_watches(
    session: AsyncSession,
    telegram_user_id: int,
    *,
    active_only: bool = True,
) -> list[PumpEntryWatchRow]:
    q = select(PumpEntryWatchRow).where(
        PumpEntryWatchRow.telegram_user_id == telegram_user_id
    )
    if active_only:
        q = q.where(PumpEntryWatchRow.status == "active")
    q = q.order_by(PumpEntryWatchRow.id.desc())
    r = await session.execute(q)
    return list(r.scalars().all())


async def get_entry_watch(session: AsyncSession, watch_id: int) -> PumpEntryWatchRow | None:
    return await session.get(PumpEntryWatchRow, watch_id)


async def set_entry_watch_status(
    session: AsyncSession,
    watch_id: int,
    *,
    status: str,
    user_id: int | None = None,
    note: str | None = None,
    completed_at: datetime | None = None,
) -> bool:
    q = update(PumpEntryWatchRow).where(PumpEntryWatchRow.id == watch_id)
    if user_id is not None:
        q = q.where(PumpEntryWatchRow.telegram_user_id == user_id)
    values: dict[str, Any] = {"status": status}
    if note is not None:
        values["completion_note"] = note[:1000]
    if completed_at is not None:
        values["completed_at"] = completed_at
    r = await session.execute(q.values(**values))
    return (r.rowcount or 0) > 0


async def update_entry_watch_check(
    session: AsyncSession,
    watch_id: int,
    *,
    metrics: dict[str, Any],
    checked_at: datetime,
    watch_plan: dict[str, Any] | None = None,
    last_llm_at: datetime | None = None,
    llm_eval_count: int | None = None,
    plan_adjusted: bool | None = None,
    analysis_history_json: str | None = None,
    high_watermark_price: float | None = None,
    current_phase: str | None = None,
    phase_history_json: str | None = None,
    last_phase_notified: str | None = None,
) -> None:
    values: dict[str, Any] = {
        "last_metrics_json": _dumps(metrics),
        "last_checked_at": checked_at,
    }
    if watch_plan is not None:
        values["watch_plan_json"] = _dumps(watch_plan)
    if last_llm_at is not None:
        values["last_llm_at"] = last_llm_at
    if llm_eval_count is not None:
        values["llm_eval_count"] = llm_eval_count
    if plan_adjusted is not None:
        values["plan_adjusted"] = plan_adjusted
    if analysis_history_json is not None:
        values["analysis_history_json"] = analysis_history_json[:20000]
    if high_watermark_price is not None:
        values["high_watermark_price"] = high_watermark_price
    if current_phase is not None:
        values["current_phase"] = current_phase[:32]
    if phase_history_json is not None:
        values["phase_history_json"] = phase_history_json[:20000]
    if last_phase_notified is not None:
        values["last_phase_notified"] = last_phase_notified[:32]
    await session.execute(
        update(PumpEntryWatchRow).where(PumpEntryWatchRow.id == watch_id).values(**values)
    )


def append_analysis_history(
    history_json: str | None,
    *,
    at_iso: str,
    entry_ok: bool,
    timing: str,
    text: str,
    reason_short: str | None = None,
) -> str:
    try:
        hist = json.loads(history_json or "[]")
        if not isinstance(hist, list):
            hist = []
    except json.JSONDecodeError:
        hist = []
    hist.append(
        {
            "at": at_iso,
            "entry_ok": entry_ok,
            "timing": timing,
            "reason_short": reason_short,
            "text": (text or "")[:3000],
        }
    )
    # храним последние 8 заключений
    hist = hist[-8:]
    return json.dumps(hist, ensure_ascii=False)


def append_phase_history(
    history_json: str | None,
    *,
    at_iso: str,
    phase: str,
    kind: str,
) -> str:
    try:
        hist = json.loads(history_json or "[]")
        if not isinstance(hist, list):
            hist = []
    except json.JSONDecodeError:
        hist = []
    hist.append({"at": at_iso, "phase": phase, "kind": kind})
    hist = hist[-20:]
    return json.dumps(hist, ensure_ascii=False)
