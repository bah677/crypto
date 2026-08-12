"""Мониторинг entry-watch: детерминированные условия → LLM re-eval → доп. алерт."""

from __future__ import annotations

import asyncio
import html
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.bybit.priority import background_request_scope, end_background_tick, try_begin_background_tick
from app.bybit.rest import BybitRest
from app.config import get_settings
from app.db.session import session_scope
from app.pump_scan.entry_watch_metrics import compute_entry_watch_metrics
from app.pump_scan.entry_watch_plan import (
    default_watch_plan,
    evaluate_watch_plan,
    format_metrics_ru,
    format_plan_summary,
    normalize_watch_plan,
)
from app.pump_scan.params import PumpScanParams
from app.repository.pump_scan import get_pump_config
from app.repository.pump_entry_watch import (
    fetch_active_entry_watches,
    set_entry_watch_status,
    update_entry_watch_check,
)
from app.services.admin_notify import _send_message
from app.services.pump_deepseek import reeval_entry_watch_async

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


def _loads(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def _metrics_line(metrics: dict) -> str:
    return format_metrics_ru(metrics)


async def run_pump_entry_watch_tick() -> None:
    if not get_settings().pump_entry_watch_enabled:
        return
    if not await asyncio.to_thread(try_begin_background_tick, "pump_entry_watch"):
        log.debug("Entry watch tick skipped — фон занят")
        return
    try:
        with background_request_scope():
            await _run_entry_watch_tick_async()
    finally:
        await asyncio.to_thread(end_background_tick)


async def _run_entry_watch_tick_async() -> None:
    async with session_scope() as session:
        watches = await fetch_active_entry_watches(session)
    if not watches:
        return

    params = PumpScanParams()
    try:
        async with session_scope() as session:
            row = await get_pump_config(session)
            params = row.params()
    except Exception:
        log.debug("entry_watch: params load failed", exc_info=True)

    client = BybitRest(category="linear")
    now = datetime.now(tz=MSK)
    settings = get_settings()
    llm_cooldown = timedelta(seconds=max(300, int(settings.pump_entry_watch_llm_cooldown_sec)))

    for watch in watches:
        if watch.expires_at and watch.expires_at <= now:
            async with session_scope() as session:
                await set_entry_watch_status(
                    session,
                    watch.id,
                    status="expired",
                    note="TTL истёк",
                    completed_at=now,
                )
                await session.commit()
            try:
                await _send_message(
                    watch.telegram_chat_id,
                    f"👀 <b>Слежение снято</b> · <code>{html.escape(watch.symbol)}</code>\n"
                    f"TTL истёк — окно по плану не подтверждено.",
                    reply_to_message_id=watch.source_message_id
                    if watch.source_chat_id == watch.telegram_chat_id
                    else None,
                )
            except Exception:
                log.exception("entry_watch expire notify #%s", watch.id)
            continue

        try:
            metrics = await asyncio.to_thread(
                compute_entry_watch_metrics,
                client,
                watch.symbol,
                impulse_price=float(watch.impulse_price),
                params=params,
            )
        except Exception:
            log.exception("entry_watch metrics failed #%s %s", watch.id, watch.symbol)
            continue

        plan = normalize_watch_plan(_loads(watch.watch_plan_json))
        ev = evaluate_watch_plan(plan, metrics)

        async with session_scope() as session:
            await update_entry_watch_check(
                session,
                watch.id,
                metrics=metrics,
                checked_at=now,
            )
            await session.commit()

        if ev.invalidated:
            note = "Инвалидация: " + ", ".join(ev.matched_invalidate)
            async with session_scope() as session:
                await set_entry_watch_status(
                    session,
                    watch.id,
                    status="invalidated",
                    note=note,
                    completed_at=now,
                )
                await session.commit()
            try:
                await _send_message(
                    watch.telegram_chat_id,
                    f"👀 <b>Слежение снято</b> · <code>{html.escape(watch.symbol)}</code>\n"
                    f"{html.escape(note)}\n"
                    f"Метрики: {_metrics_line(metrics)}",
                    reply_to_message_id=watch.source_message_id
                    if watch.source_chat_id == watch.telegram_chat_id
                    else None,
                )
            except Exception:
                log.exception("entry_watch invalidate notify #%s", watch.id)
            continue

        if not ev.triggered:
            continue

        # Triggered — LLM re-eval with cooldown
        if watch.last_llm_at and (now - watch.last_llm_at) < llm_cooldown:
            log.debug(
                "entry_watch #%s triggered but LLM cooldown",
                watch.id,
            )
            continue

        reeval = None
        try:
            reeval = await reeval_entry_watch_async(
                symbol=watch.symbol,
                alert_text=watch.alert_text or "",
                watch_plan=plan,
                metrics=metrics,
                baseline=_loads(watch.baseline_metrics_json),
            )
        except Exception:
            log.exception("entry_watch LLM reeval #%s", watch.id)

        new_count = int(watch.llm_eval_count or 0) + 1
        adjust = None
        if reeval and reeval.adjust_plan and not watch.plan_adjusted and not reeval.entry_ok:
            adjust = reeval.adjust_plan

        async with session_scope() as session:
            await update_entry_watch_check(
                session,
                watch.id,
                metrics=metrics,
                checked_at=now,
                watch_plan=adjust,
                last_llm_at=now,
                llm_eval_count=new_count,
                plan_adjusted=True if adjust else None,
            )
            await session.commit()

        if reeval is None:
            # без LLM — если план сработал, шлём консервативный алерт «условия плана»
            msg = (
                f"👀 <b>Условия слежения</b> · <code>{html.escape(watch.symbol)}</code>\n"
                f"План выполнен ({html.escape(format_plan_summary(plan))}).\n"
                f"Метрики: {_metrics_line(metrics)}\n"
                f"<i>DeepSeek недоступен — проверьте вручную, окно может быть открыто.</i>"
            )
            try:
                await _send_message(
                    watch.telegram_chat_id,
                    msg,
                    reply_to_message_id=watch.source_message_id
                    if watch.source_chat_id == watch.telegram_chat_id
                    else None,
                )
            except Exception:
                log.exception("entry_watch fallback notify #%s", watch.id)
            # не закрываем — ждём LLM или инвалидации
            continue

        if reeval.entry_ok:
            async with session_scope() as session:
                await set_entry_watch_status(
                    session,
                    watch.id,
                    status="done",
                    note=reeval.reason_short or "entry_ok",
                    completed_at=now,
                )
                await session.commit()
            body = html.escape(reeval.text.strip())
            msg = (
                f"✅ <b>Окно входа</b> · <code>{html.escape(watch.symbol)}</code>\n"
                f"Метрики: {_metrics_line(metrics)}\n\n"
                f"🤖 <b>DeepSeek</b>\n{body}"
            )
            try:
                await _send_message(
                    watch.telegram_chat_id,
                    msg,
                    reply_to_message_id=watch.source_message_id
                    if watch.source_chat_id == watch.telegram_chat_id
                    else None,
                )
                # также reply в исходный канал, если это другой чат
                if (
                    watch.source_chat_id
                    and watch.source_message_id
                    and watch.source_chat_id != watch.telegram_chat_id
                ):
                    await _send_message(
                        watch.source_chat_id,
                        msg,
                        reply_to_message_id=watch.source_message_id,
                    )
            except Exception:
                log.exception("entry_watch entry_ok notify #%s", watch.id)
            continue

        # still early — optionally adjusted plan already saved
        note = reeval.reason_short or "ещё рано"
        try:
            await _send_message(
                watch.telegram_chat_id,
                f"👀 <b>Пока рано</b> · <code>{html.escape(watch.symbol)}</code>\n"
                f"{html.escape(note)}\n"
                f"Метрики: {_metrics_line(metrics)}"
                + (
                    f"\nПлан обновлён: {html.escape(format_plan_summary(adjust))}"
                    if adjust
                    else ""
                ),
            )
        except Exception:
            log.exception("entry_watch wait notify #%s", watch.id)


async def create_watch_from_context(
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    symbol: str,
    source_chat_id: int | None,
    source_message_id: int | None,
    impulse_price: float | None = None,
    impulse_interval: str = "15",
    alert_text: str = "",
    watch_plan: dict | None = None,
) -> tuple[bool, str]:
    """Создать watch. Возвращает (ok, message_html)."""
    settings = get_settings()
    if not settings.pump_entry_watch_enabled:
        return False, "Слежение выключено в конфиге."

    from app.repository.pump_entry_watch import (
        cancel_duplicate_active_watches,
        count_active_user_watches,
        create_entry_watch,
        find_active_user_symbol_watch,
        find_active_watch_for_source,
        get_latest_suggestion_for_symbol,
        get_suggestion_for_message,
    )

    now = datetime.now(tz=MSK)
    max_n = int(settings.pump_entry_watch_max_per_user)
    sym = symbol.upper()

    def _already_msg(row) -> str:
        return (
            f"👀 <b>{html.escape(sym)}</b> уже на слежении "
            f"(#{row.id}).\n"
            f"Повторно не добавляю — смотрите /pump_watches"
        )

    async with session_scope() as session:
        if source_chat_id is not None and source_message_id is not None:
            by_msg = await find_active_watch_for_source(
                session,
                telegram_user_id=telegram_user_id,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
            )
            if by_msg is not None:
                return True, _already_msg(by_msg)

        existing = await find_active_user_symbol_watch(
            session, telegram_user_id=telegram_user_id, symbol=sym
        )
        if existing is not None:
            # подчистить случайные копии, оставить самый ранний
            n_dup = await cancel_duplicate_active_watches(
                session,
                keep_id=existing.id,
                telegram_user_id=telegram_user_id,
                symbol=sym,
                now=now,
            )
            await session.commit()
            if n_dup:
                log.info(
                    "entry_watch dedupe user=%s %s kept=#%s cancelled=%s",
                    telegram_user_id,
                    sym,
                    existing.id,
                    n_dup,
                )
            return True, _already_msg(existing)

        n = await count_active_user_watches(session, telegram_user_id)
        if n >= max_n:
            return False, f"Лимит активных слежений: {max_n}. Снимите лишние: /pump_watches"

        suggestion = None
        if source_chat_id is not None and source_message_id is not None:
            suggestion = await get_suggestion_for_message(
                session,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
                now=now,
            )
        if suggestion is None:
            suggestion = await get_latest_suggestion_for_symbol(
                session, symbol=sym, now=now
            )

        plan = normalize_watch_plan(
            watch_plan
            or (_loads(suggestion.watch_plan_json) if suggestion else None)
            or default_watch_plan()
        )
        price = impulse_price
        interval = impulse_interval
        text = alert_text
        if suggestion is not None:
            if price is None:
                price = float(suggestion.impulse_price)
            interval = suggestion.impulse_interval or interval
            if not text:
                text = suggestion.alert_text or ""

    if price is None or price <= 0:
        client = BybitRest(category="linear")
        try:
            price = await asyncio.to_thread(client.last_price, sym)
        except Exception:
            price = None
        if price is None or price <= 0:
            return False, "Не удалось получить цену импульса."

    params = PumpScanParams()
    try:
        async with session_scope() as session:
            row = await get_pump_config(session)
            params = row.params()
    except Exception:
        pass

    client = BybitRest(category="linear")
    try:
        baseline = await asyncio.to_thread(
            compute_entry_watch_metrics,
            client,
            sym,
            impulse_price=float(price),
            params=params,
        )
    except Exception:
        log.exception("baseline metrics %s", sym)
        baseline = {"price": float(price)}

    ttl_h = int(plan.get("ttl_hours") or 24)
    expires = now + timedelta(hours=ttl_h)

    async with session_scope() as session:
        # повторная проверка перед INSERT (гонка двойного клика)
        existing = await find_active_user_symbol_watch(
            session, telegram_user_id=telegram_user_id, symbol=sym
        )
        if existing is not None:
            return True, _already_msg(existing)
        if source_chat_id is not None and source_message_id is not None:
            by_msg = await find_active_watch_for_source(
                session,
                telegram_user_id=telegram_user_id,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
            )
            if by_msg is not None:
                return True, _already_msg(by_msg)

        row = await create_entry_watch(
            session,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            symbol=sym,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            impulse_price=float(price),
            impulse_interval=interval,
            alert_text=text,
            watch_plan=plan,
            baseline_metrics=baseline,
            expires_at=expires,
        )
        # если параллельно всё же вставили копии — оставить этот id
        await cancel_duplicate_active_watches(
            session,
            keep_id=row.id,
            telegram_user_id=telegram_user_id,
            symbol=sym,
            now=now,
        )
        await session.commit()
        wid = row.id

    log.info(
        "Entry watch created #%s %s user=%s expires=%s",
        wid,
        sym,
        telegram_user_id,
        expires.isoformat(),
    )
    return True, (
        f"👀 Поставил <code>{html.escape(sym)}</code> на слежение "
        f"(#{wid})\n\n"
        f"<b>Что жду:</b> {html.escape(format_plan_summary(plan))}\n"
        f"<b>Сейчас:</b> {html.escape(format_metrics_ru(baseline))}\n\n"
        f"Проверяю примерно раз в {settings.pump_entry_watch_interval_sec // 60 or 1} мин, "
        f"до {expires.strftime('%d.%m %H:%M')} MSK.\n"
        f"Список и снятие: /pump_watches"
    )
