"""Debug ATR Pullback: JSONL на символ + краткая строка в .log."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.atr_pullback.logic import in_interest_zone
from app.atr_pullback.tasks import AtrPullbackTask
from app.config import PROJECT_ROOT, get_settings
from app.indicators.ema import ema_series

MSK = ZoneInfo("Europe/Moscow")
_DEBUG_DIR = PROJECT_ROOT / "logs" / "atr_pullback"
_locks: dict[str, threading.Lock] = {}


def debug_enabled() -> bool:
    return get_settings().atr_pullback_debug_enabled


def debug_dir() -> Path:
    s = get_settings()
    raw = (s.atr_pullback_debug_dir or "").strip()
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


def zone_snapshot(
    closes: list[float],
    bar_index: int,
    ema_fast: int,
    ema_slow: int,
    side: str | None,
) -> dict[str, Any]:
    fast = ema_series(closes, ema_fast)
    slow = ema_series(closes, ema_slow)
    if bar_index < 0 or bar_index >= len(closes):
        return {"error": "index_out_of_range"}
    close = closes[bar_index]
    f, s = fast[bar_index], slow[bar_index]
    out: dict[str, Any] = {
        "close": round(close, 8),
        "ema_fast": round(f, 8) if f is not None else None,
        "ema_slow": round(s, 8) if s is not None else None,
    }
    if f is not None:
        out["above_fast"] = close > f
    if s is not None:
        out["above_slow"] = close > s
    if side and f is not None and s is not None:
        out["zone_ok"] = in_interest_zone(side, close, fast, slow, bar_index)
    return out


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
            f.write(summary + "\n")


def _format_summary(rec: dict[str, Any]) -> str:
    ts = rec.get("ts", "")
    kind = rec.get("kind", "?")
    decision = rec.get("decision", "?")
    state = f"{rec.get('state_before')}→{rec.get('state_after')}"
    side = rec.get("armed_side") or "—"
    btf = rec.get("btf") or {}
    mtf = rec.get("mtf") or {}
    btf_s = f"BTF cross={btf.get('cross')} zone={btf.get('zone', {}).get('zone_ok')}"
    mtf_s = (
        f"MTF dist={mtf.get('pullback_dist_atr')} ok={mtf.get('pullback_ok')} "
        f"atr={mtf.get('atr')}"
    )
    entry = rec.get("entry") or {}
    entry_s = ""
    if entry:
        entry_s = f" entry={entry.get('status')}"
    return f"{ts} [{kind}] {decision} {state} {side} | {btf_s} | {mtf_s}{entry_s}"


def base_record(task: AtrPullbackTask, *, kind: str) -> dict[str, Any]:
    return {
        "ts": _iso_now(),
        "kind": kind,
        "task_id": task.db_id,
        "symbol": task.symbol,
        "btf_interval": task.btf_interval,
        "mtf_interval": task.mtf_interval,
        "ema_fast": task.ema_fast,
        "ema_slow": task.ema_slow,
        "state_before": task.state,
        "armed_side": task.armed_side,
        "cross_price": task.cross_price,
        "auto_trade": task.auto_trade,
    }
