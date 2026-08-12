"""Тексты UI для ТВХ: параметры и вотчлист."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.pump_scan.params import FIELD_LABELS, PumpScanParams
from app.pump_scan.timeframes import interval_label

MSK = ZoneInfo("Europe/Moscow")
_WATCH_PAGE = 12


def _pct_share(val: float) -> str:
    return f"{val * 100:.0f}%"


def format_tvh_params_block(params: PumpScanParams) -> str:
    one_shot = "да" if params.tvh_one_shot_watch else "нет"
    return (
        "<b>Параметры ТВХ</b>\n"
        f"TTL вотчлиста: <b>{params.tvh_watch_ttl_min}</b> мин\n"
        f"Мин. score: <b>{params.tvh_min_score}</b>/100\n"
        f"EMA: <b>{params.tvh_ema_fast}</b> / <b>{params.tvh_ema_slow}</b> (младший TF)\n"
        f"Фейд: откат ≥ <b>{_pct_share(params.tvh_min_retrace_fade)}</b> · "
        f"swing <b>{params.tvh_swing_lookback}</b> бар\n"
        f"Продолжение: откат <b>{_pct_share(params.tvh_pullback_min)}</b>–"
        f"<b>{_pct_share(params.tvh_pullback_max)}</b>\n"
        f"Один алерт и снять: <b>{one_shot}</b>\n"
        f"Монитор: каждую минуту <b>:18 MSK</b>"
    )


def _alert_status(done: bool) -> str:
    return "✅" if done else "⏳"


def _fmt_score(val: int | None, min_score: int) -> str:
    if val is None:
        return "—"
    if val >= min_score:
        return f"<b>{val}</b>"
    return str(val)


def _watch_line(
    row,
    *,
    short_score: int | None = None,
    long_score: int | None = None,
    min_score: int = 55,
) -> str:
    raw = row.hit_dict()
    pct = float(raw.get("price_change_pct", 0))
    rvol = float(raw.get("rvol", 0))
    sign = "+" if pct > 0 else ""
    icon = "🔥" if row.impulse_direction == "pump" else "🔻"
    tf_src = interval_label(row.source_interval)
    tf_ent = interval_label(row.entry_interval)
    exp = row.expires_at.astimezone(MSK).strftime("%H:%M MSK")
    if row.impulse_direction == "pump":
        short_lbl = "шорт-фейд"
        long_lbl = "лонг-продолж."
    else:
        short_lbl = "шорт-продолж."
        long_lbl = "лонг-фейд"
    return (
        f"{icon} <code>{row.symbol}</code> · {tf_src}→{tf_ent}\n"
        f"   Импульс <b>{sign}{pct:.1f}%</b> · RVOL <b>×{rvol:.1f}</b>\n"
        f"   Score: шорт {_fmt_score(short_score, min_score)} · "
        f"лонг {_fmt_score(long_score, min_score)} "
        f"<i>(порог {min_score})</i>\n"
        f"   {_alert_status(row.alerted_short)} {short_lbl} · "
        f"{_alert_status(row.alerted_long)} {long_lbl} · до <b>{exp}</b>"
    )


def format_tvh_watchlist_page(
    watches: list,
    page: int,
    *,
    scores: dict[int, tuple[int | None, int | None]] | None = None,
    min_score: int = 55,
) -> tuple[str, int, int]:
    total = len(watches)
    pages = max(1, (total + _WATCH_PAGE - 1) // _WATCH_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = watches[page * _WATCH_PAGE : (page + 1) * _WATCH_PAGE]

    lines = ["<b>Вотчлист ТВХ</b>"]
    if total:
        lines.append(f"Активных: <b>{total}</b> · стр. <b>{page + 1}/{pages}</b>")
    else:
        lines.append("Сейчас пуст — ждём импульсы от сканера.")
    lines.append("")
    lines.append(
        "<i>Импульс → вотчлист → алерт в топик только при score ≥ порога.</i>"
    )
    lines.append("")

    if chunk:
        score_map = scores or {}
        blocks: list[str] = []
        for row in chunk:
            short_s, long_s = score_map.get(row.id, (None, None))
            blocks.append(
                _watch_line(
                    row,
                    short_score=short_s,
                    long_score=long_s,
                    min_score=min_score,
                )
            )
        lines.append("\n\n".join(blocks))
    else:
        lines.append("<i>После pump/dump скана монеты появятся здесь.</i>")

    return "\n".join(lines), page, pages


def format_tvh_home(params: PumpScanParams, watches: list) -> str:
    now = datetime.now(MSK).strftime("%H:%M MSK")
    pending = sum(
        1
        for w in watches
        if not (w.alerted_short and w.alerted_long)
    )
    return (
        "<b>ТВХ · мониторинг</b>\n"
        f"Обновлено: {now}\n\n"
        f"{format_tvh_params_block(params)}\n\n"
        f"Вотчлист: <b>{len(watches)}</b> · ожидают сигнал: <b>{pending}</b>"
    )


def tvh_field_labels_short() -> list[tuple[str, str]]:
    keys = [
        "tvh_watch_ttl_min",
        "tvh_min_score",
        "tvh_ema_fast",
        "tvh_ema_slow",
        "tvh_min_retrace_fade",
        "tvh_pullback_min",
        "tvh_pullback_max",
        "tvh_swing_lookback",
        "tvh_one_shot_watch",
    ]
    return [(FIELD_LABELS[k], k) for k in keys]
