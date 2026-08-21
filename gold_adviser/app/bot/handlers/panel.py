from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.bot.admin_guard import invalidate_admin_cache, is_admin_user
from app.bot.panel_ui import format_status, panel_keyboard
from app.config import get_settings
from app.db.session import session_scope
from app.market.candles import median_step_seconds
from app.market.provider import fetch_xau_candles
from app.repository.admins import add_admin, list_admins, remove_admin
from app.services.anomaly import analyze_body_anomaly
from app.services.chart import render_m1_candles_png
from app.services.scan import run_gold_scan_tick
from app.services.settings_cache import settings_cache

log = logging.getLogger(__name__)
router = Router(name="gold")

_MULT_STEP = 0.25
_LOOK_STEP = 5
_TTL_STEP = 5


async def _require_admin(message: Message) -> bool:
    uid = message.from_user.id if message.from_user else 0
    if not await is_admin_user(uid):
        await message.answer("Нет доступа.")
        return False
    return True


@router.message(Command("start", "help"))
async def cmd_start(message: Message) -> None:
    uid = message.from_user.id if message.from_user else None
    log.info("cmd_start from uid=%s chat=%s text=%r", uid, message.chat.id, message.text)
    try:
        cfg = await settings_cache.get()
        sent = await message.answer(
            format_status(cfg, generation=settings_cache.generation)
            + "\n\nКоманды: /gold · /on · /off · /status · /chart · /admins",
            reply_markup=panel_keyboard(cfg),
        )
        log.info("cmd_start replied message_id=%s", sent.message_id)
    except Exception:
        log.exception("cmd_start failed")
        try:
            await message.answer("Ошибка панели — смотрите logs/")
        except Exception:
            log.exception("cmd_start fallback reply failed")


@router.message(Command("status", "gold"))
async def cmd_status(message: Message) -> None:
    cfg = await settings_cache.get()
    await message.answer(
        format_status(cfg, generation=settings_cache.generation),
        reply_markup=panel_keyboard(cfg),
    )


@router.message(Command("on"))
async def cmd_on(message: Message) -> None:
    if not message.from_user:
        return
    cfg = await settings_cache.set(updated_by=message.from_user.id, enabled=True)
    await message.answer(
        "✅ Сканер включён.\n" + format_status(cfg, generation=settings_cache.generation),
        reply_markup=panel_keyboard(cfg),
    )


@router.message(Command("off"))
async def cmd_off(message: Message) -> None:
    if not message.from_user:
        return
    cfg = await settings_cache.set(updated_by=message.from_user.id, enabled=False)
    await message.answer(
        "⏸ Сканер выключен.\n" + format_status(cfg, generation=settings_cache.generation),
        reply_markup=panel_keyboard(cfg),
    )


@router.message(Command("admins"))
async def cmd_admins(message: Message) -> None:
    async with session_scope() as session:
        rows = await list_admins(session)
    if not rows:
        await message.answer("Таблица admins пуста.")
        return
    lines = ["<b>Админы gold_adviser</b>"]
    for r in rows:
        note = f" — {r.note}" if r.note else ""
        lines.append(f"<code>{r.telegram_user_id}</code>{note}")
    await message.answer("\n".join(lines))


@router.message(Command("admin_add"))
async def cmd_admin_add(message: Message) -> None:
    s = get_settings()
    uid = message.from_user.id if message.from_user else 0
    if uid != s.superadmin_telegram_id:
        await message.answer("Только супер-админ из .env.")
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>/admin_add USER_ID</code>")
        return
    target = int(parts[1])
    async with session_scope() as session:
        await add_admin(session, target)
    invalidate_admin_cache(target)
    await message.answer(f"Добавлен админ <code>{target}</code>")


@router.message(Command("admin_del"))
async def cmd_admin_del(message: Message) -> None:
    s = get_settings()
    uid = message.from_user.id if message.from_user else 0
    if uid != s.superadmin_telegram_id:
        await message.answer("Только супер-админ из .env.")
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>/admin_del USER_ID</code>")
        return
    target = int(parts[1])
    if target == s.superadmin_telegram_id:
        await message.answer("Супер-админа удалить нельзя.")
        return
    async with session_scope() as session:
        ok = await remove_admin(session, target)
    invalidate_admin_cache(target)
    if ok:
        await message.answer(f"Удалён <code>{target}</code>")
    else:
        await message.answer(f"Нет в admins: <code>{target}</code>")


@router.callback_query(F.data == "gold:noop")
async def cb_noop(cb: CallbackQuery) -> None:
    await cb.answer()


@router.callback_query(F.data == "gold:refresh")
async def cb_refresh(cb: CallbackQuery) -> None:
    settings_cache.invalidate()
    cfg = await settings_cache.get()
    if cb.message:
        await cb.message.edit_text(
            format_status(cfg, generation=settings_cache.generation),
            reply_markup=panel_keyboard(cfg),
        )
    await cb.answer("Обновлено")


@router.callback_query(F.data == "gold:toggle")
async def cb_toggle(cb: CallbackQuery) -> None:
    if not cb.from_user:
        return
    cur = await settings_cache.get()
    cfg = await settings_cache.set(updated_by=cb.from_user.id, enabled=not cur.enabled)
    if cb.message:
        await cb.message.edit_text(
            format_status(cfg, generation=settings_cache.generation),
            reply_markup=panel_keyboard(cfg),
        )
    await cb.answer("Включён" if cfg.enabled else "Выключен")


@router.callback_query(F.data.in_({"gold:mult:+", "gold:mult:-"}))
async def cb_mult(cb: CallbackQuery) -> None:
    if not cb.from_user or not cb.data:
        return
    cur = await settings_cache.get()
    delta = _MULT_STEP if cb.data.endswith("+") else -_MULT_STEP
    new_v = round(max(1.0, min(cur.body_mult + delta, 10.0)), 2)
    cfg = await settings_cache.set(updated_by=cb.from_user.id, body_mult=new_v)
    if cb.message:
        await cb.message.edit_text(
            format_status(cfg, generation=settings_cache.generation),
            reply_markup=panel_keyboard(cfg),
        )
    await cb.answer(f"×{cfg.body_mult:g}")


@router.callback_query(F.data.in_({"gold:look:+", "gold:look:-"}))
async def cb_look(cb: CallbackQuery) -> None:
    if not cb.from_user or not cb.data:
        return
    cur = await settings_cache.get()
    delta = _LOOK_STEP if cb.data.endswith("+") else -_LOOK_STEP
    new_v = max(5, min(cur.lookback + delta, 200))
    cfg = await settings_cache.set(updated_by=cb.from_user.id, lookback=new_v)
    if cb.message:
        await cb.message.edit_text(
            format_status(cfg, generation=settings_cache.generation),
            reply_markup=panel_keyboard(cfg),
        )
    await cb.answer(f"{cfg.lookback} свечей")


@router.callback_query(F.data.in_({"gold:ttl:+", "gold:ttl:-"}))
async def cb_ttl(cb: CallbackQuery) -> None:
    if not cb.from_user or not cb.data:
        return
    cur = await settings_cache.get()
    delta = _TTL_STEP if cb.data.endswith("+") else -_TTL_STEP
    new_v = max(5, min(cur.settings_cache_ttl_sec + delta, 600))
    cfg = await settings_cache.set(
        updated_by=cb.from_user.id, settings_cache_ttl_sec=new_v
    )
    if cb.message:
        await cb.message.edit_text(
            format_status(cfg, generation=settings_cache.generation),
            reply_markup=panel_keyboard(cfg),
        )
    await cb.answer(f"TTL {cfg.settings_cache_ttl_sec}с")


@router.callback_query(F.data == "gold:scan_now")
async def cb_scan_now(cb: CallbackQuery) -> None:
    await cb.answer("Сканирую…")
    try:
        await run_gold_scan_tick()
        if cb.message:
            await cb.message.answer("Скан выполнен (если была аномалия — уйдёт алерт).")
    except Exception:
        log.exception("manual scan failed")
        if cb.message:
            await cb.message.answer("Ошибка скана — смотрите logs/")


async def _send_m1_chart(message: Message, *, limit: int | None = None) -> None:
    cfg = await settings_cache.get()
    n = max(5, min(int(limit if limit is not None else cfg.lookback), 200))
    wait = await message.answer(f"Строю график {n}×M1…")
    try:
        candles, source = await fetch_xau_candles(limit=n)
        anomaly = analyze_body_anomaly(candles, body_mult=cfg.body_mult)
        avg_body = anomaly.avg_body if anomaly else None
        step = median_step_seconds(candles) or 60.0
        tf_label = "M1" if abs(step - 60) <= 15 else f"~{int(round(step / 60))}m"
        png = await asyncio.to_thread(
            render_m1_candles_png,
            candles,
            title=f"XAU · {tf_label} · {source}",
            highlight_last=True,
            avg_body=avg_body,
        )
        last = candles[-1]
        first = candles[0]
        caption = (
            f"<b>XAU {tf_label}</b> · {len(candles)} св (окно={n}) · "
            f"<code>{source}</code> · шаг ~{int(round(step))}с\n"
            f"<code>{first.open_time_key}</code> → <code>{last.open_time_key}</code>\n"
            f"last O={last.open:.2f} H={last.high:.2f} L={last.low:.2f} C={last.close:.2f}"
        )
        if anomaly:
            # как в скане: среднее тело по всем кроме last; порог = avg × body_mult
            n_avg = max(len(candles) - 1, 1)
            threshold = anomaly.avg_body * cfg.body_mult
            caption += (
                f"\nсреднее тело: <b>{anomaly.avg_body:.2f}</b> "
                f"(по {n_avg} св из {len(candles)})\n"
                f"алерт при теле ≥ <b>{threshold:.2f}</b> "
                f"(×{cfg.body_mult:g})\n"
                f"тело last: <b>{anomaly.body:.2f}</b> "
                f"(×{anomaly.ratio:.2f} к среднему)"
            )
        await message.answer_photo(
            BufferedInputFile(png, filename="xau_m1.png"),
            caption=caption,
        )
    except Exception:
        log.exception("chart failed")
        await message.answer("Не удалось построить график — смотрите logs/")
    finally:
        try:
            await wait.delete()
        except Exception:
            pass


@router.message(Command("chart"))
async def cmd_chart(message: Message) -> None:
    await _send_m1_chart(message)


@router.callback_query(F.data == "gold:chart")
async def cb_chart(cb: CallbackQuery) -> None:
    await cb.answer()
    if cb.message:
        await _send_m1_chart(cb.message)
