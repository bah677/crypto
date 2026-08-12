"""Debug Scalp M5/M1: JSONL на символ + читаемый .log."""

from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import PROJECT_ROOT, get_settings
from app.scalp_advisor.strategy_params import ScalpStrategyParams
from app.scalp_advisor.tasks import ScalpAdvisorTask

MSK = ZoneInfo("Europe/Moscow")
_DEBUG_DIR = PROJECT_ROOT / "logs" / "scalp_advisor"
_locks: dict[str, threading.Lock] = {}

_STEP_ORDER = ("m5", "noise", "bb", "entry", "room")


def debug_enabled() -> bool:
    return get_settings().scalp_advisor_debug_enabled


def debug_dir() -> Path:
    s = get_settings()
    raw = (s.scalp_advisor_debug_dir or "").strip()
    return Path(raw) if raw else _DEBUG_DIR


def _lock_for(symbol: str) -> threading.Lock:
    sym = symbol.upper()
    if sym not in _locks:
        _locks[sym] = threading.Lock()
    return _locks[sym]


def _iso_now() -> str:
    return datetime.now(tz=MSK).isoformat(timespec="seconds")


def bar_time_msk(open_ms: int) -> str:
    return datetime.fromtimestamp(open_ms / 1000, tz=MSK).strftime("%Y-%m-%d %H:%M")


def rotate_debug_logs(
    symbol: str,
    params: ScalpStrategyParams,
    *,
    task_id: int | None = None,
    reason: str = "config",
) -> None:
    if not debug_enabled():
        return
    sym = symbol.upper()
    d = debug_dir()
    arc = d / "arc"
    arc.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=MSK).strftime("%Y%m%d_%H%M%S")
    header = "\n".join(params.debug_header_lines(symbol=sym, task_id=task_id))
    header += f"\n# rotate: {reason}\n\n"

    with _lock_for(sym):
        for ext in (".jsonl", ".log"):
            src = d / f"{sym}{ext}"
            if src.exists() and src.stat().st_size > 0:
                shutil.move(str(src), str(arc / f"{stamp}_{sym}{ext}"))
        with (d / f"{sym}.log").open("w", encoding="utf-8") as f:
            f.write(header)
        with (d / f"{sym}.jsonl").open("w", encoding="utf-8") as f:
            rec = {
                "ts": _iso_now(),
                "kind": "config",
                "symbol": sym,
                "task_id": task_id,
                "reason": reason,
                "revision": params.revision,
                "fingerprint": params.fingerprint(),
            }
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_record(symbol: str, record: dict[str, Any]) -> None:
    if not debug_enabled():
        return
    sym = symbol.upper()
    d = debug_dir()
    d.mkdir(parents=True, exist_ok=True)
    jsonl_path = d / f"{sym}.jsonl"
    log_path = d / f"{sym}.log"

    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    summary = _format_summary(record)

    with _lock_for(sym):
        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(summary)
            if not summary.endswith("\n"):
                f.write("\n")


def _side_ru(side: str) -> str:
    return "LONG" if side == "Buy" else "SHORT"


def _fail_step(fail: str | None) -> str | None:
    if not fail:
        return None
    return fail.split(":", 1)[0]


def _step_skipped(side: dict[str, Any], step: str) -> bool:
    if side.get("ok"):
        return False
    fail = side.get("fail")
    if not fail:
        return False
    failed = _fail_step(fail)
    if failed is None or failed not in _STEP_ORDER:
        return False
    if step not in _STEP_ORDER:
        return False
    return _STEP_ORDER.index(step) > _STEP_ORDER.index(failed)


def _line(mark: str, text: str, detail: str = "") -> str:
    tail = f" · {detail}" if detail else ""
    return f"    {mark} {text}{tail}"


def _fmt_num(v: Any, digits: int = 2) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{digits}g}"
    return str(v)


def _format_m5(side: str, m5: dict[str, Any], skipped: bool) -> list[str]:
    if skipped:
        return [_line("—", "M5 setup", "не проверено")]
    lines: list[str] = []
    fail = m5.get("fail")
    cross = m5.get("cross_age_bars")
    pb_max = m5.get("pullback_max", 1.5)

    if fail == "no_cross" or cross is None:
        lines.append(_line("✗", "M5 кросс EMA20/50 за 24 св", "нет"))
        return lines
    lines.append(_line("✓", "M5 кросс EMA20/50", f"{cross} св назад"))

    trend = "EMA20 > EMA50" if side == "Buy" else "EMA20 < EMA50"
    if fail == "fast_below_slow" and side == "Buy":
        lines.append(_line("✗", trend, "EMA20 ≤ EMA50"))
        return lines
    if fail == "fast_above_slow" and side == "Sell":
        lines.append(_line("✗", trend, "EMA20 ≥ EMA50"))
        return lines
    lines.append(_line("✓", trend))

    pb = m5.get("pullback_atr")
    if fail == "pullback_too_far":
        lines.append(_line("✗", "M5 откат к EMA20", f"{_fmt_num(pb)} ATR > {pb_max}"))
    else:
        lines.append(_line("✓", "M5 откат к EMA20", f"{_fmt_num(pb)} ATR (≤ {pb_max})"))
    return lines


def _format_noise(side: str, noise: dict[str, Any], skipped: bool) -> list[str]:
    if skipped:
        return [_line("—", "M1 шум / импульс", "не проверено")]
    lines: list[str] = []
    fail = noise.get("fail")
    adx = noise.get("adx")
    adx_min = noise.get("adx_min", 20)

    if fail == "adx_low":
        lines.append(_line("✗", "M1 ADX", f"{_fmt_num(adx)} ≤ {adx_min}"))
        return lines
    lines.append(_line("✓", "M1 ADX", f"{_fmt_num(adx)} > {adx_min}"))

    bc = noise.get("body_bars")
    bc_min = noise.get("body_bars_min", 6)
    if fail == "body_bars":
        lines.append(_line("✗", "M1 тела свечей (10 св)", f"{bc}/{bc_min} с телом >30%"))
        return lines
    lines.append(_line("✓", "M1 тела свечей (10 св)", f"{bc}/{bc_min}"))

    imp = "3 close вверх" if side == "Buy" else "3 close вниз"
    if fail == "impulse_not_up" and side == "Buy":
        lines.append(_line("✗", f"M1 импульс ({imp})", str(noise.get("last3_closes", ""))))
        return lines
    if fail == "impulse_not_down" and side == "Sell":
        lines.append(_line("✗", f"M1 импульс ({imp})", str(noise.get("last3_closes", ""))))
        return lines
    lines.append(_line("✓", f"M1 импульс ({imp})"))
    return lines


def _format_bb(side: str, bb: dict[str, Any], skipped: bool) -> list[str]:
    if skipped:
        return [_line("—", "Bollinger M1 (20,2)", "не проверено")]
    lines: list[str] = []
    fail = bb.get("fail")
    bw = bb.get("bandwidth")
    bw_min = bb.get("min_bandwidth", 0.0015)

    if fail == "squeeze":
        lines.append(_line("✗", "BB не сжатие", f"bandwidth {_fmt_num(bw, 4)} < {bw_min}"))
        return lines
    lines.append(_line("✓", "BB не сжатие", f"bandwidth {_fmt_num(bw, 4)}"))

    pos = "close ≤ middle" if side == "Buy" else "close ≥ middle"
    if fail == "close_above_middle":
        lines.append(_line("✗", pos, f"close={bb.get('close')} mid={bb.get('middle')}"))
        return lines
    if fail == "close_below_middle":
        lines.append(_line("✗", pos, f"close={bb.get('close')} mid={bb.get('middle')}"))
        return lines
    lines.append(_line("✓", pos))
    return lines


def _format_entry_pat(side: str, ent: dict[str, Any], skipped: bool) -> list[str]:
    if skipped:
        return [_line("—", "M1 паттерн у EMA20", "не проверено")]
    lines: list[str] = []
    fail = ent.get("fail")
    t_max = ent.get("touch_max", 1.0)

    if fail == "close_above_ema":
        lines.append(_line("✗", "M1 close у EMA20", "слишком высоко"))
        return lines
    if fail == "close_below_ema":
        lines.append(_line("✗", "M1 close у EMA20", "слишком низко"))
        return lines
    lines.append(_line("✓", "M1 close у EMA20"))

    touch = ent.get("ema_touch_atr")
    if fail == "no_ema_touch":
        lines.append(_line("✗", "M1 касание EMA20", f"{_fmt_num(touch)} ATR > {t_max}"))
        return lines
    lines.append(_line("✓", "M1 касание EMA20", f"{_fmt_num(touch)} ATR (≤ {t_max})"))

    if fail == "no_pattern":
        lines.append(_line("✗", "M1 pin / engulfing", "нет паттерна"))
        return lines
    lines.append(_line("✓", "M1 pin / engulfing", ent.get("pattern") or ""))
    return lines


def _format_room(room: dict[str, Any], skipped: bool) -> list[str]:
    if skipped:
        return [_line("—", "Запас до TP1", "не проверено")]
    fail = room.get("fail")
    r_atr = room.get("room_atr")
    r_min = room.get("room_min", 1.5)

    if fail == "no_levels_above":
        return [_line("✗", "2 уровня TP выше entry", "нет")]
    if fail == "no_levels_below":
        return [_line("✗", "2 уровня TP ниже entry", "нет")]
    if fail == "room_too_small":
        return [_line("✗", "Запас до TP1", f"{_fmt_num(r_atr)} ATR < {r_min}")]
    return [
        _line(
            "✓",
            "Запас до TP1",
            f"{_fmt_num(r_atr)} ATR (≥ {r_min}) · TP1={room.get('tp1')} TP2={room.get('tp2')}",
        )
    ]


def _format_side_block(side: str, side_data: dict[str, Any]) -> list[str]:
    out = [f"  [{_side_ru(side)}]"]
    if side_data.get("ok"):
        out.append("    >>> все условия выполнены")
    elif side_data.get("fail"):
        out.append(f"    стоп: {side_data['fail']}")

    for step, formatter in (
        ("m5", lambda sk: _format_m5(side, side_data.get("m5") or {}, sk)),
        ("noise", lambda sk: _format_noise(side, side_data.get("noise") or {}, sk)),
        ("bb", lambda sk: _format_bb(side, side_data.get("bb") or {}, sk)),
        ("entry", lambda sk: _format_entry_pat(side, side_data.get("entry") or {}, sk)),
        ("room", lambda sk: _format_room(side_data.get("room") or {}, sk)),
    ):
        out.extend(formatter(_step_skipped(side_data, step)))
    return out


def _format_entry_eval(rec: dict[str, Any]) -> str:
    decision = rec.get("decision", "?")
    m1 = rec.get("m1") or {}
    entry = rec.get("entry") or {}
    signal = rec.get("signal") or {}
    ts = rec.get("ts", "")
    sym = rec.get("symbol", "?")
    tid = rec.get("task_id", "?")
    m1_t = m1.get("bar_time", "")

    lines: list[str] = []

    if decision == "open":
        side = signal.get("side", entry.get("winner", "?"))
        lines.append("")
        lines.append("=" * 60)
        lines.append(
            f"{ts} | *** OPEN {_side_ru(side)} *** | {sym} #{tid} | M1 {m1_t}"
        )
        lines.append("=" * 60)
    else:
        lines.append(f"{ts} | eval | {decision} | {sym} #{tid} | M1 {m1_t}")

    if not rec.get("in_trading_hours", True) and decision == "outside_hours":
        lines.append("  ✗ вне расписания торговли задания")
        lines.append("")
        return "\n".join(lines)

    if entry.get("fail") == "insufficient_bars":
        bars = entry.get("bars") or {}
        lines.append(f"  ✗ мало баров M1={bars.get('m1')} M5={bars.get('m5')}")
        lines.append("")
        return "\n".join(lines)

    if entry.get("fail") == "m5_align":
        lines.append("  ✗ не удалось синхронизировать M5 с закрытием M1")
        lines.append("")
        return "\n".join(lines)

    sides = entry.get("sides") or {}
    for side_key in ("Buy", "Sell"):
        if side_key in sides:
            lines.extend(_format_side_block(side_key, sides[side_key]))
            lines.append("")

    if decision == "open" and signal:
        lines.append(
            f"  >>> ENTRY {_fmt_num(signal.get('entry'), 4)}"
            f" | SL {_fmt_num(signal.get('sl'), 4)}"
            f" | TP1 {_fmt_num(signal.get('tp1'), 4)}"
            f" | TP2 {_fmt_num(signal.get('tp2'), 4)}"
            f" | {signal.get('pattern', '')}"
        )
        lines.append("=" * 60)
        lines.append("")
    elif decision == "no_signal":
        lines.append("  итог: ни LONG, ни SHORT не прошли все фильтры")
        lines.append("")

    return "\n".join(lines)


def _format_manage(rec: dict[str, Any]) -> str:
    ts = rec.get("ts", "")
    decision = rec.get("decision", "?")
    sym = rec.get("symbol", "?")
    manage = rec.get("manage") or {}
    notify = " → TG" if rec.get("notify") else ""

    if decision.startswith("close_"):
        reason = decision.replace("close_", "").upper()
        pnl = manage.get("pnl_r")
        pnl_s = f" {pnl:+.2f}R" if pnl is not None else ""
        return (
            f"{ts} | *** CLOSE {reason} *** | {sym}"
            f" | exit {manage.get('exit')}{pnl_s}{notify}\n"
        )

    if decision == "sl_update":
        return (
            f"{ts} | SL update | {sym}"
            f" | {manage.get('sl_prev')} → {manage.get('sl')}"
            f" | mark {manage.get('mark')}"
            f" | TP1={'✓' if manage.get('tp1_hit') else '·'}"
            f" TP2={'✓' if manage.get('tp2_hit') else '·'}{notify}\n"
        )

    return (
        f"{ts} | manage | {decision} | {sym}"
        f" | mark {manage.get('mark')} sl {manage.get('sl')}{notify}\n"
    )


def _format_summary(rec: dict[str, Any]) -> str:
    decision = rec.get("decision", "?")
    kind = rec.get("kind", "?")

    if kind == "manage" or decision.startswith("close_") or decision in ("hold", "sl_update"):
        return _format_manage(rec)

    if decision in ("open", "no_signal", "outside_hours") or (rec.get("entry") and decision not in ("same_m1",)):
        return _format_entry_eval(rec)

    ts = rec.get("ts", "")
    m1 = rec.get("m1") or {}
    return (
        f"{ts} | {decision} | trade={rec.get('trade_state', '?')}"
        f" | M1 new={m1.get('new_bar')} {m1.get('bar_time', '')}\n"
    )


def base_record(task: ScalpAdvisorTask, *, kind: str) -> dict[str, Any]:
    return {
        "ts": _iso_now(),
        "kind": kind,
        "task_id": task.db_id,
        "symbol": task.symbol,
        "alias": task.alias,
        "trade_state": task.trade_state,
        "trade_side": task.trade_side,
        "levels_count": len(task.levels),
    }
