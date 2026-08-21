from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.advisor.tasks import AdvisorTask, advisor_task_from_row
from app.bot.help_text import build_help_parts, build_status_text
from app.bot.keyboards import cancel_kb, main_menu_kb
from app.bot.states import FundingScanStates
from app.config import get_settings
from app.db.session import session_scope
from app.repository.advisor_tasks import fetch_all_advisor_tasks
from app.repository.admins import add_admin, is_telegram_admin, list_admins, remove_admin
from app.bot.admin_guard import invalidate_admin_cache, is_admin_user

router = Router()


async def _load_advisor_tasks_for_ui() -> list[AdvisorTask]:
    async with session_scope() as session:
        rows = await fetch_all_advisor_tasks(session)
    return [advisor_task_from_row(r) for r in rows]


async def _send_help(target: Message, settings, tasks: list[AdvisorTask]) -> None:
    for i, part in enumerate(build_help_parts(settings, tasks)):
        if i == 0:
            await target.answer(part)
        else:
            await target.answer(part)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    s = get_settings()
    args = ""
    if message.text and " " in message.text:
        args = message.text.split(maxsplit=1)[1].strip()
    pump_hint = ""
    if args.startswith("pump"):
        pump_hint = (
            "\n\nТеперь можно нажать кнопку ордера в pump-алерте группы — "
            "мастер откроется здесь, в личке."
        )
    if s.is_advisor_mode:
        tasks = await _load_advisor_tasks_for_ui()
        enabled_n = sum(1 for t in tasks if t.enabled)
        intro = (
            "Режим <b>советчик</b>: сигналы EMA с Bybit в этот чат, без ордеров.\n"
            f"Заданий в БД: <b>{len(tasks)}</b> (включено: <b>{enabled_n}</b>).\n"
            "Новое задание: /task_add · список: /tasks · справка: /help"
        )
    else:
        intro = (
            "Режим <b>автоторговли</b> (Bybit / MT5).\n"
            "Создавайте задания через меню. Справка: /help"
        )
    await message.answer(
        intro + pump_hint,
        reply_markup=main_menu_kb(advisor_mode=s.is_advisor_mode),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    s = get_settings()
    tasks = await _load_advisor_tasks_for_ui() if s.is_advisor_mode else []
    await _send_help(message, s, tasks)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    s = get_settings()
    tasks = await _load_advisor_tasks_for_ui() if s.is_advisor_mode else []
    await message.answer(build_status_text(s, tasks))


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    s = get_settings()
    is_admin = await is_admin_user(uid)
    async with session_scope() as session:
        in_db = await is_telegram_admin(session, uid)
    await message.answer(
        f"Ваш Telegram user id: <code>{uid}</code>\n"
        f"SUPERADMIN_TELEGRAM_ID в .env: <code>{s.superadmin_telegram_id}</code>\n"
        f"Таблица admins: <b>{'да' if in_db else 'нет'}</b>\n"
        + (
            "✅ Доступ к боту разрешён."
            if is_admin
            else "⛔ Нет доступа — добавьте id в таблицу <code>admins</code>."
        )
    )


@router.message(Command("admins"))
async def cmd_admins(message: Message) -> None:
    async with session_scope() as session:
        rows = await list_admins(session)
    if not rows:
        await message.answer("Таблица <code>admins</code> пуста.", parse_mode="HTML")
        return
    lines = ["<b>Администраторы бота</b> (таблица <code>admins</code>):", ""]
    for row in rows:
        note = f" — {row.note}" if row.note else ""
        lines.append(f"• <code>{row.telegram_user_id}</code>{note}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("admin_add"))
async def cmd_admin_add(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    s = get_settings()
    if uid != s.superadmin_telegram_id:
        await message.answer("⛔ Добавлять админов может только SUPERADMIN_TELEGRAM_ID из .env.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: <code>/admin_add TELEGRAM_USER_ID</code>", parse_mode="HTML")
        return
    raw = parts[1].strip()
    if not raw.isdigit():
        await message.answer("Укажите числовой Telegram user id.")
        return
    target = int(raw)
    async with session_scope() as session:
        await add_admin(session, target)
    invalidate_admin_cache(target)
    await message.answer(f"✅ Добавлен в admins: <code>{target}</code>", parse_mode="HTML")


@router.message(Command("admin_del"))
async def cmd_admin_del(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    s = get_settings()
    if uid != s.superadmin_telegram_id:
        await message.answer("⛔ Удалять админов может только SUPERADMIN_TELEGRAM_ID из .env.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: <code>/admin_del TELEGRAM_USER_ID</code>", parse_mode="HTML")
        return
    raw = parts[1].strip()
    if not raw.isdigit():
        await message.answer("Укажите числовой Telegram user id.")
        return
    target = int(raw)
    if target == s.superadmin_telegram_id:
        await message.answer("Нельзя удалить супер-админа из .env — он восстанавливается при старте.")
        return
    async with session_scope() as session:
        removed = await remove_admin(session, target)
    invalidate_admin_cache(target)
    if removed:
        await message.answer(f"✅ Удалён из admins: <code>{target}</code>", parse_mode="HTML")
    else:
        await message.answer(f"В admins нет <code>{target}</code>.", parse_mode="HTML")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    s = get_settings()
    await message.answer(
        "Сценарий отменён.",
        reply_markup=main_menu_kb(advisor_mode=s.is_advisor_mode),
    )


@router.message(Command("funding", "funding_scan"))
async def cmd_funding(message: Message, state: FSMContext) -> None:
    s = get_settings()
    await state.set_state(FundingScanStates.top_n)
    await message.answer(
        "Ручной funding scan.\n\n"
        "Шаг 1/2. Сколько <b>топ альтов</b> по капитализации взять?\n"
        f"Число от 1 до 250, или <code>-</code> для значения из .env "
        f"(сейчас <code>{s.funding_top_n}</code>).",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


def _parse_funding_top_n(raw: str, default: int) -> int:
    text = raw.strip()
    if text == "-":
        return default
    if not text.isdigit():
        raise ValueError("Введите целое число 1–250 или `-`.")
    n = int(text)
    if n < 1 or n > 250:
        raise ValueError("Число должно быть от 1 до 250.")
    return n


def _parse_funding_threshold(raw: str, default: float) -> float:
    text = raw.strip().replace(",", ".")
    if text == "-":
        return default
    try:
        v = float(text)
    except ValueError as e:
        raise ValueError("Введите число (порог |годовые| %) или `-`.") from e
    if v <= 0:
        raise ValueError("Порог должен быть больше 0.")
    return v


@router.message(FundingScanStates.top_n, F.text)
async def funding_st_top_n(message: Message, state: FSMContext) -> None:
    s = get_settings()
    try:
        top_n = _parse_funding_top_n(message.text, s.funding_top_n)
    except ValueError as e:
        await message.answer(str(e))
        return

    await state.update_data(funding_top_n=top_n)
    await state.set_state(FundingScanStates.threshold)
    await message.answer(
        "Шаг 2/2. Порог <b>|годовые funding|</b> в %?\n"
        f"Число больше 0, или <code>-</code> для .env "
        f"(сейчас <code>{s.funding_annual_threshold:.0f}</code>).\n"
        f"Будет скан топ-<b>{top_n}</b> альтов.",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(FundingScanStates.threshold, F.text)
async def funding_st_threshold(message: Message, state: FSMContext) -> None:
    s = get_settings()
    try:
        threshold = _parse_funding_threshold(message.text, s.funding_annual_threshold)
    except ValueError as e:
        await message.answer(str(e))
        return

    data = await state.get_data()
    top_n = int(data["funding_top_n"])
    await state.clear()

    await message.answer(
        f"Сканирую funding… топ-{top_n}, порог |годовые| > {threshold:.0f}%"
    )
    from app.services.funding_scan import format_funding_scan_message, run_funding_scan

    try:
        hits = await run_funding_scan(
            notify=False,
            force=True,
            top_n=top_n,
            threshold_annual=threshold,
        )
    except Exception:
        await message.answer("Ошибка скана — см. err.log")
        return

    text = format_funding_scan_message(
        hits,
        threshold_annual=threshold,
        top_n=top_n,
    )
    await message.answer(text)


@router.callback_query(F.data == "advisor:help")
async def cb_advisor_help(callback: CallbackQuery) -> None:
    s = get_settings()
    tasks = await _load_advisor_tasks_for_ui() if s.is_advisor_mode else []
    await _send_help(callback.message, s, tasks)
    await callback.answer()


@router.callback_query(F.data == "advisor:status")
async def cb_advisor_status(callback: CallbackQuery) -> None:
    s = get_settings()
    tasks = await _load_advisor_tasks_for_ui() if s.is_advisor_mode else []
    await callback.message.answer(build_status_text(s, tasks))
    await callback.answer()
