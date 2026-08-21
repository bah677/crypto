"""План слежения до входа: каталог метрик, default plan, оценка условий."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

ALLOWED_METRICS = frozenset(
    {
        "funding_trajectory_state",
        "funding_now",
        "funding_min",
        "oi_trend",
        "oi_chg_window_pct",
        "squeeze_phase",
        "dist_atr_nearest",
        "dist_atr_ema200",
        "price_vs_impulse_high_pct",
        "price_vs_high_watermark_pct",
        "climax_signal",
    }
)

ALLOWED_OPS = frozenset({"eq", "ne", "in", "not_in", "gte", "lte", "gt", "lt"})

ENTRY_TIMINGS = frozenset({"now", "early", "late", "skip", "unknown"})

_DEFAULT_TTL_HOURS = 72


def default_watch_plan(*, ttl_hours: int = _DEFAULT_TTL_HOURS) -> dict[str, Any]:
    """Железобетонный план, если LLM не отдал JSON."""
    return {
        "ttl_hours": int(ttl_hours),
        "all_of": [
            {
                "metric": "funding_trajectory_state",
                "op": "eq",
                "value": "peak_reversing",
            },
            {
                "metric": "oi_trend",
                "op": "in",
                "value": ["falling", "flat"],
            },
        ],
        "any_of": [],
        "invalidate_if": [],
    }


def _normalize_condition(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    metric = str(raw.get("metric") or "").strip()
    op = str(raw.get("op") or "").strip().lower()
    if metric not in ALLOWED_METRICS or op not in ALLOWED_OPS:
        return None
    if "value" not in raw:
        return None
    return {"metric": metric, "op": op, "value": raw["value"]}


def normalize_watch_plan(raw: Any, *, fallback_ttl: int = _DEFAULT_TTL_HOURS) -> dict[str, Any]:
    """Приводит plan к безопасному виду; неизвестные метрики отбрасывает."""
    base = default_watch_plan(ttl_hours=fallback_ttl)
    if not isinstance(raw, dict):
        return base

    try:
        ttl = int(raw.get("ttl_hours") or fallback_ttl)
    except (TypeError, ValueError):
        ttl = fallback_ttl
    ttl = max(_DEFAULT_TTL_HOURS, min(ttl, 72))

    def _list(key: str) -> list[dict[str, Any]]:
        items = raw.get(key)
        if not isinstance(items, list):
            return list(base[key])
        out: list[dict[str, Any]] = []
        for it in items:
            c = _normalize_condition(it)
            if c is not None:
                out.append(c)
        return out

    all_of = _list("all_of")
    any_of = _list("any_of") if isinstance(raw.get("any_of"), list) else []
    invalidate = _list("invalidate_if")
    # Для pump-in-downtrend рост цены сам по себе не инвалидирует идею.
    # Legacy-правило `price_vs_impulse_high_pct >= X` только мешает вести сквиз до капитуляции.
    invalidate = [
        c for c in invalidate if c.get("metric") != "price_vs_impulse_high_pct"
    ]
    if not all_of and not any_of:
        return default_watch_plan(ttl_hours=ttl)
    return {
        "ttl_hours": ttl,
        "all_of": all_of or list(base["all_of"]),
        "any_of": any_of,
        "invalidate_if": invalidate or list(base["invalidate_if"]),
    }


def _cmp(actual: Any, op: str, expected: Any) -> bool | None:
    """None = метрика недоступна / сравнение невозможно."""
    if actual is None:
        return None
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        if not isinstance(expected, (list, tuple, set)):
            return None
        return actual in expected
    if op == "not_in":
        if not isinstance(expected, (list, tuple, set)):
            return None
        return actual not in expected
    try:
        a = float(actual)
        e = float(expected)
    except (TypeError, ValueError):
        return None
    if op == "gte":
        return a >= e
    if op == "lte":
        return a <= e
    if op == "gt":
        return a > e
    if op == "lt":
        return a < e
    return None


def eval_condition(cond: dict[str, Any], metrics: dict[str, Any]) -> bool | None:
    return _cmp(metrics.get(cond["metric"]), cond["op"], cond["value"])


@dataclass(frozen=True)
class PlanEvalResult:
    triggered: bool
    invalidated: bool
    all_of_ok: bool
    any_of_ok: bool
    failed_all_of: list[str] = field(default_factory=list)
    matched_invalidate: list[str] = field(default_factory=list)


def evaluate_watch_plan(plan: dict[str, Any], metrics: dict[str, Any]) -> PlanEvalResult:
    all_conds = plan.get("all_of") or []
    any_conds = plan.get("any_of") or []
    inv_conds = plan.get("invalidate_if") or []

    failed: list[str] = []
    all_ok = True
    for c in all_conds:
        r = eval_condition(c, metrics)
        if r is not True:
            all_ok = False
            failed.append(f"{c['metric']} {c['op']} {c['value']}")

    if not any_conds:
        any_ok = True
    else:
        any_ok = False
        for c in any_conds:
            if eval_condition(c, metrics) is True:
                any_ok = True
                break

    matched_inv: list[str] = []
    invalidated = False
    for c in inv_conds:
        if eval_condition(c, metrics) is True:
            invalidated = True
            matched_inv.append(f"{c['metric']} {c['op']} {c['value']}")

    return PlanEvalResult(
        triggered=bool(all_ok and any_ok and not invalidated),
        invalidated=invalidated,
        all_of_ok=all_ok,
        any_of_ok=any_ok,
        failed_all_of=failed,
        matched_invalidate=matched_inv,
    )


def hit_suggests_early_entry(hit: Any) -> bool:
    """По композитному индикатору: ещё рано / риск продолжения."""
    fot = getattr(hit, "funding_oi", None)
    if fot is None:
        return False
    state = getattr(fot, "funding_trajectory_state", None)
    oi = getattr(fot, "oi_trend", None)
    if state == "extending":
        return True
    if state == "peak_reversing" and oi == "rising":
        return True
    line = getattr(fot, "alert_line", None) or ""
    if "рано" in line.lower():
        return True
    return False


def should_offer_entry_watch(*, hit: Any, entry_timing: str | None) -> bool:
    timing = (entry_timing or "unknown").strip().lower()
    if timing == "early":
        return True
    if timing in ("now", "late", "skip"):
        return timing == "now" and hit_suggests_early_entry(hit)
    return hit_suggests_early_entry(hit)


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S | re.I)
_JSON_TAIL = re.compile(r"(\{[^{}]*\"entry_timing\"[^{}]*\})", re.S)


@dataclass(frozen=True)
class ParsedDeepseekWatch:
    entry_timing: str
    watch_if_early: bool
    watch_plan: dict[str, Any]
    reason_short: str | None = None


def parse_deepseek_watch_block(raw_text: str) -> tuple[str, ParsedDeepseekWatch | None]:
    """
    Вырезает JSON-блок из ответа LLM.
    Возвращает (текст_для_пользователя, parsed|None).
    """
    text = (raw_text or "").strip()
    if not text:
        return "", None

    blob: str | None = None
    m = _JSON_FENCE.search(text)
    if m:
        blob = m.group(1)
        user_text = (text[: m.start()] + text[m.end() :]).strip()
    else:
        # последний JSON-объект с entry_timing
        found = None
        for m2 in re.finditer(r"\{[^{}]*\"entry_timing\"[^{}]*\}", text, flags=re.S):
            found = m2
        if found:
            blob = found.group(0)
            user_text = (text[: found.start()] + text[found.end() :]).strip()
        else:
            return text, None

    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return text, None
    if not isinstance(data, dict):
        return user_text or text, None

    timing = str(data.get("entry_timing") or "unknown").strip().lower()
    if timing not in ENTRY_TIMINGS:
        timing = "unknown"
    watch_if = bool(data.get("watch_if_early", timing == "early"))
    plan = normalize_watch_plan(data.get("watch_plan"))
    reason = data.get("reason_short")
    reason_s = str(reason).strip() if reason else None
    return user_text or text, ParsedDeepseekWatch(
        entry_timing=timing,
        watch_if_early=watch_if,
        watch_plan=plan,
        reason_short=reason_s,
    )


_METRIC_RU = {
    "funding_trajectory_state": "состояние фандинга",
    "funding_now": "фандинг сейчас",
    "funding_min": "минимум фандинга",
    "oi_trend": "тренд OI",
    "oi_chg_window_pct": "изменение OI",
    "squeeze_phase": "фаза сквиза",
    "dist_atr_nearest": "дистанция до EMA (ATR)",
    "dist_atr_ema200": "дистанция до EMA200 (ATR)",
    "price_vs_impulse_high_pct": "цена vs импульс",
    "price_vs_high_watermark_pct": "цена vs локальный хай",
    "climax_signal": "признак climax",
}

_STATE_RU = {
    "peak_reversing": "фандинг разворачивается после экстремума",
    "extending": "сквиз ещё в разгаре",
    "normalized": "фандинг уже нормализовался",
    "no_extreme": "экстремума фандинга нет",
    "unknown": "данные по фандингу недоступны",
    "falling": "падает",
    "rising": "растёт",
    "flat": "плоский",
    "squeeze_building": "сквиз разгоняется",
    "squeeze_deep": "глубокий сквиз",
    "at_resistance": "у сильного уровня",
    "capitulation": "капитуляция шортов",
    "entry_ready": "окно входа",
    "cooled": "импульс выдохся",
}

_OP_RU = {
    "eq": "станет",
    "ne": "не будет",
    "in": "будет одним из",
    "not_in": "не будет среди",
    "gte": "≥",
    "lte": "≤",
    "gt": ">",
    "lt": "<",
}


def _ru_value(metric: str, value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(_ru_value(metric, v) for v in value)
    if isinstance(value, str):
        return _STATE_RU.get(value, value)
    if metric in (
        "funding_now",
        "funding_min",
        "oi_chg_window_pct",
        "price_vs_impulse_high_pct",
        "price_vs_high_watermark_pct",
    ):
        try:
            return f"{float(value):.0f}%"
        except (TypeError, ValueError):
            return str(value)
    if metric in ("dist_atr_nearest", "dist_atr_ema200"):
        try:
            return f"{float(value):.1f} ATR"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def format_condition_ru(cond: dict[str, Any]) -> str:
    metric = str(cond.get("metric") or "")
    op = str(cond.get("op") or "")
    value = cond.get("value")
    name = _METRIC_RU.get(metric, metric)
    op_s = _OP_RU.get(op, op)
    val_s = _ru_value(metric, value)
    if metric == "funding_trajectory_state" and op == "eq":
        return f"дождаться: {val_s}"
    if metric == "oi_trend" and op == "in":
        vals = value if isinstance(value, (list, tuple)) else [value]
        ru = [_STATE_RU.get(str(v), str(v)) for v in vals]
        if len(ru) == 2:
            return f"OI {ru[0]} или {ru[1]}"
        return f"OI: {', '.join(ru)}"
    if metric == "squeeze_phase" and op == "eq":
        return f"фаза: {val_s}"
    if metric == "price_vs_impulse_high_pct" and op in ("gte", "gt"):
        return f"снять, если цена уйдёт выше импульса на {val_s}"
    if metric == "price_vs_high_watermark_pct" and op in ("lte", "lt"):
        return f"цена откатит от локального хая на {val_s}"
    return f"{name} {op_s} {val_s}"


def format_plan_summary(plan: dict[str, Any]) -> str:
    """Человекочитаемое описание плана (для Telegram)."""
    ttl = plan.get("ttl_hours", "?")
    lines: list[str] = [f"следим до {ttl} ч"]
    for c in plan.get("all_of") or []:
        lines.append(format_condition_ru(c))
    for c in plan.get("any_of") or []:
        lines.append("или: " + format_condition_ru(c))
    for c in plan.get("invalidate_if") or []:
        lines.append(format_condition_ru(c))
    return "; ".join(lines)


_PHASE_PROFILES: dict[str, dict[str, str]] = {
    "squeeze_building": {
        "title": "Сквиз разгоняется",
        "body": (
            "Шорты под давлением: фандинг уходит в минус, OI тянется вверх. "
            "Ранняя фаза — до точки входа ещё далеко."
        ),
        "action": "Не шортим. Ждём углубления сквиза или подхода к крупной EMA.",
    },
    "squeeze_deep": {
        "title": "Глубокий сквиз",
        "body": (
            "Фандинг на экстремуме, OI ещё растёт — рынок агрессивно выжимает шортистов. "
            "Рост цены от импульса может ускориться, это часть сценария."
        ),
        "action": "Терпение: не вход, а подготовка. Следим за уровнем и признаками выдыхания.",
    },
    "at_resistance": {
        "title": "У сильного уровня",
        "body": (
            "Цена вплотную к крупной EMA — зона, где сквиз часто тормозит. "
            "Смотрим, удержат ли покупатели уровень или пойдёт откат."
        ),
        "action": "Ключевая зона. Ждём разворот фандинга и ослабление OI — там обычно окно для шорта.",
    },
    "capitulation": {
        "title": "Капитуляция шортов",
        "body": (
            "Фандинг развернулся после пика, но OI ещё не сдался. "
            "Похоже на финальную фазу выжимания — шорты закрываются, но не все."
        ),
        "action": "Почти готово. Держим на слежении — ждём подтверждения по OI и композитному сигналу.",
    },
    "entry_ready": {
        "title": "Окно входа открыто",
        "body": (
            "Фандинг разворачивается, OI сдаёт позиции — классическая картина "
            "после сквиза для fade-входа."
        ),
        "action": "Проверяем уровень и риск. Если сетап подтверждается — можно рассматривать шорт.",
    },
    "cooled": {
        "title": "Импульс выдохся",
        "body": (
            "Фандинг нормализовался — агрессивный сквиз, похоже, закончился. "
            "Сетап мог устареть или перейти в боковик."
        ),
        "action": "Пересматриваем идею: либо ждём новый импульс, либо снимаем слежение.",
    },
}


def phase_title_ru(phase: str) -> str:
    """Короткий заголовок фазы для алертов."""
    profile = _PHASE_PROFILES.get(phase, {})
    return profile.get("title") or _STATE_RU.get(phase, phase)


def format_phase_transition_ru(
    *,
    prev_phase: str | None,
    new_phase: str,
    metrics: dict[str, Any],
) -> str:
    """Текст уведомления о смене фазы — для Telegram (plain text, без HTML)."""
    profile = _PHASE_PROFILES.get(new_phase, {})
    lines: list[str] = []

    if prev_phase and prev_phase != new_phase:
        prev_l = _STATE_RU.get(prev_phase, prev_phase)
        new_l = _STATE_RU.get(new_phase, new_phase)
        lines.append(f"Смена фазы: {prev_l} → {new_l}")

    body = profile.get("body", "")
    if body:
        lines.append(body)

    action = profile.get("action", "")
    if action:
        lines.append(f"💡 {action}")

    snap = format_metrics_ru(metrics)
    if snap and snap != "данных пока мало":
        lines.append(f"📊 {snap}")

    return "\n".join(lines) if lines else _STATE_RU.get(new_phase, new_phase)


def format_metrics_ru(metrics: dict[str, Any]) -> str:
    """Текущий снимок метрик по-русски."""
    parts: list[str] = []

    if metrics.get("funding_now") is not None:
        try:
            fv = float(metrics["funding_now"])
            parts.append(f"фандинг {fv:+.0f}% год.")
        except (TypeError, ValueError):
            pass

    st = metrics.get("funding_trajectory_state")
    if st and str(st) not in ("unknown", "no_extreme"):
        parts.append(_STATE_RU.get(str(st), str(st)))

    oi = metrics.get("oi_trend")
    if oi and str(oi) != "unknown":
        parts.append(f"OI {_STATE_RU.get(str(oi), str(oi))}")

    dist200 = metrics.get("dist_atr_ema200")
    if dist200 is not None:
        try:
            d = float(dist200)
            if d >= 0:
                parts.append(f"над EMA200 на {d:.1f} ATR")
            else:
                parts.append(f"под EMA200 на {abs(d):.1f} ATR")
        except (TypeError, ValueError):
            pass
    elif metrics.get("dist_atr_nearest") is not None:
        try:
            parts.append(f"до EMA {float(metrics['dist_atr_nearest']):.1f} ATR")
        except (TypeError, ValueError):
            pass

    px = metrics.get("price_vs_impulse_high_pct")
    if px is not None:
        try:
            parts.append(f"от импульса {float(px):+.1f}%")
        except (TypeError, ValueError):
            pass

    px_hwm = metrics.get("price_vs_high_watermark_pct")
    if px_hwm is not None:
        try:
            v = float(px_hwm)
            if abs(v) >= 0.05:
                parts.append(f"от локального хая {v:+.1f}%")
        except (TypeError, ValueError):
            pass

    return " · ".join(parts) if parts else "данных пока мало"


def classify_squeeze_phase(metrics: dict[str, Any]) -> str:
    """Фаза сквиза для pump-in-downtrend watch."""
    funding_state = str(metrics.get("funding_trajectory_state") or "unknown")
    oi_trend = str(metrics.get("oi_trend") or "unknown")
    funding_now = metrics.get("funding_now")
    dist_near = metrics.get("dist_atr_nearest")

    try:
        funding_abs = abs(float(funding_now)) if funding_now is not None else 0.0
    except (TypeError, ValueError):
        funding_abs = 0.0
    try:
        near = float(dist_near) if dist_near is not None else None
    except (TypeError, ValueError):
        near = None

    if funding_state == "peak_reversing" and oi_trend in ("falling", "flat"):
        return "entry_ready"
    if funding_state == "peak_reversing":
        return "capitulation"
    if funding_state == "normalized":
        return "cooled"
    if near is not None and near <= 1.0 and funding_state in ("extending", "peak_reversing"):
        return "at_resistance"
    if funding_abs >= 1000.0 and oi_trend == "rising":
        return "squeeze_deep"
    if funding_state in ("extending", "no_extreme") and oi_trend in ("rising", "unknown"):
        return "squeeze_building"
    return "squeeze_building"
