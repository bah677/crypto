"""Параметры стратегии Scalp M5/M1: вкл/выкл условий и числа."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from typing import Any

# Порядок условий фиксирован (для UI и debug)
CONDITION_ORDER: tuple[str, ...] = (
    "m5_cross",
    "m5_trend",
    "m5_pullback",
    "m1_adx",
    "m1_bodies",
    "m1_impulse",
    "bb",
    "m1_entry",
    "room_tp",
)

CONDITION_LABELS: dict[str, str] = {
    "m5_cross": "1. M5 кросс EMA",
    "m5_trend": "2. M5 тренд EMA fast/slow",
    "m5_pullback": "3. M5 откат к EMA",
    "m1_adx": "4. M1 ADX",
    "m1_bodies": "5. M1 тела свечей",
    "m1_impulse": "6. M1 импульс (3 close)",
    "bb": "7. Bollinger M1",
    "m1_entry": "8. M1 паттерн у EMA20",
    "room_tp": "9. Запас до TP1",
}

CONDITION_HELP: dict[str, str] = {
    "m5_cross": "За последние N M5 был кросс EMA в сторону сделки.",
    "m5_trend": "Быстрая EMA выше/ниже медленной на M5 (тренд по направлению).",
    "m5_pullback": "Цена на M5 близко к быстрой EMA — откат, не перегрев.",
    "m1_adx": "На M1 достаточно движения: ADX выше порога.",
    "m1_bodies": "В окне M1 достаточно свечей с нормальным телом, не одни дожи.",
    "m1_impulse": "3 последних close на M1 идут в сторону входа.",
    "bb": "Полосы не в сжатии; close по нужную сторону от середины BB.",
    "m1_entry": "Касание EMA20 + pin/поглощение на последней M1.",
    "room_tp": "До ближайшего TP1 из уровней задания хватает запаса в ATR.",
}

# Поля для меню «выбрать что менять»: группа → список имён полей
EDIT_GROUPS: dict[str, tuple[str, ...]] = {
    "m5_cross": ("m5_cross_lookback",),
    "m5_pullback": ("m5_pullback_max_atr",),
    "m1_adx": ("m1_adx_min",),
    "m1_bodies": ("m1_body_window", "m1_body_bars_min", "m1_body_ratio"),
    "bb": ("bb_period", "bb_std", "bb_min_bandwidth"),
    "m1_entry": ("m1_touch_max_atr", "m1_close_ema_eps_atr"),
    "room_tp": ("room_tp_min_atr",),
    "global": ("ema_fast", "ema_slow", "atr_period", "adx_period", "sl_atr_mult"),
}

FIELD_LABELS: dict[str, str] = {
    "m5_cross_lookback": "Lookback, M5 св",
    "m5_pullback_max_atr": "Max откат, ATR",
    "m1_adx_min": "Min ADX",
    "m1_body_window": "Окно свечей M1",
    "m1_body_bars_min": "Min свечей с телом",
    "m1_body_ratio": "Доля тела (0–1)",
    "bb_period": "BB period",
    "bb_std": "BB std",
    "bb_min_bandwidth": "Min bandwidth",
    "m1_touch_max_atr": "Касание EMA, ATR",
    "m1_close_ema_eps_atr": "Close у EMA, ATR",
    "room_tp_min_atr": "Min запас до TP1, ATR",
    "ema_fast": "EMA fast",
    "ema_slow": "EMA slow",
    "atr_period": "ATR period",
    "adx_period": "ADX period",
    "sl_atr_mult": "SL × ATR",
}

FIELD_CONDITION: dict[str, str] = {
    "m5_cross_lookback": "m5_cross",
    "m5_pullback_max_atr": "m5_pullback",
    "m1_adx_min": "m1_adx",
    "m1_body_window": "m1_bodies",
    "m1_body_bars_min": "m1_bodies",
    "m1_body_ratio": "m1_bodies",
    "bb_period": "bb",
    "bb_std": "bb",
    "bb_min_bandwidth": "bb",
    "m1_touch_max_atr": "m1_entry",
    "m1_close_ema_eps_atr": "m1_entry",
    "room_tp_min_atr": "room_tp",
    "ema_fast": "global",
    "ema_slow": "global",
    "atr_period": "global",
    "adx_period": "global",
    "sl_atr_mult": "global",
}

FIELD_INPUT_HINT: dict[str, str] = {
    "m5_cross_lookback": "Целое число свечей M5, напр. <code>24</code>",
    "m5_pullback_max_atr": "Число в ATR, напр. <code>1.5</code>",
    "m1_adx_min": "Число ADX, напр. <code>20</code> или <code>22</code>",
    "m1_body_window": "Целое число свечей M1, напр. <code>10</code>",
    "m1_body_bars_min": "Целое число, напр. <code>6</code>",
    "m1_body_ratio": "Доля от 0 до 1, напр. <code>0.3</code> (= 30%)",
    "bb_period": "Целое число, напр. <code>20</code>",
    "bb_std": "Число, напр. <code>2.0</code>",
    "bb_min_bandwidth": "Доля ширины, напр. <code>0.0015</code> (0.15%)",
    "m1_touch_max_atr": "Число в ATR, напр. <code>1.0</code>",
    "m1_close_ema_eps_atr": "Число в ATR, напр. <code>0.01</code>",
    "room_tp_min_atr": "Число в ATR(M1), напр. <code>1.5</code>",
    "ema_fast": "Целое число, напр. <code>20</code>",
    "ema_slow": "Целое число, напр. <code>50</code>",
    "atr_period": "Целое число, напр. <code>14</code>",
    "adx_period": "Целое число, напр. <code>14</code>",
    "sl_atr_mult": "Число, напр. <code>1.5</code>",
}

FIELD_EFFECT: dict[str, str] = {
    "m5_cross_lookback": (
        "Окно поиска кросса EMA на M5. Больше — допускается более старый кросс (больше сигналов); "
        "меньше — только свежий кросс (строже)."
    ),
    "m5_pullback_max_atr": (
        "Макс. расстояние цены от EMA fast на M5 в единицах ATR. Меньше — вход только у EMA; "
        "больше — можно входить дальше от EMA (риск перегрева)."
    ),
    "m1_adx_min": (
        "Порог ADX на M1. Ниже порога сигнал отклоняется как «рынок вялый». "
        "Выше значение — меньше входов, только при сильном движении."
    ),
    "m1_body_window": (
        "Сколько последних M1 свечей анализируются на «нормальные» тела. "
        "Больше окно — фильтр смотрит на более длинный участок."
    ),
    "m1_body_bars_min": (
        "Сколько свечей в окне должны иметь тело больше заданной доли диапазона. "
        "Больше — строже отсекает пилу и дожи."
    ),
    "m1_body_ratio": (
        "Минимальная доля тела свечи от high−low. Напр. 0.3 = тело ≥ 30% диапазона. "
        "Выше — только «направленные» свечи."
    ),
    "bb_period": "Период расчёта Bollinger на M1. Влияет на сглаженность полос.",
    "bb_std": "Ширина полос Bollinger. Больше std — шире канал, мягче фильтр.",
    "bb_min_bandwidth": (
        "Мин. ширина полос (сжатие). Ниже порога рынок считается «в сжатии» — вход блокируется."
    ),
    "m1_touch_max_atr": (
        "Насколько близко цена должна коснуться EMA20 за 3 M1 (в ATR). "
        "Меньше — вход только при точном касании EMA."
    ),
    "m1_close_ema_eps_atr": (
        "Допуск close от EMA20 на последней M1 (в ATR). "
        "Меньше — close должен быть ближе к EMA для LONG/SHORT."
    ),
    "room_tp_min_atr": (
        "Мин. запас от входа до ближайшего TP1 (уровень из задания) в ATR(M1). "
        "Больше — не входим, если до первой цели слишком близко."
    ),
    "ema_fast": "Период быстрой EMA — кросс, тренд, откат, паттерн входа.",
    "ema_slow": "Период медленной EMA — тренд на M5 и кросс.",
    "atr_period": "Период ATR для всех расчётов расстояний и SL на M5/M1.",
    "adx_period": "Период ADX на M1 для фильтра движения.",
    "sl_atr_mult": "Множитель ATR для начального SL при открытии сигнала.",
}


@dataclass
class ScalpStrategyParams:
    ema_fast: int = 20
    ema_slow: int = 50
    atr_period: int = 14
    adx_period: int = 14
    sl_atr_mult: float = 1.5

    m5_cross_enabled: bool = True
    m5_cross_lookback: int = 24

    m5_trend_enabled: bool = True

    m5_pullback_enabled: bool = True
    m5_pullback_max_atr: float = 1.5

    m1_adx_enabled: bool = True
    m1_adx_min: float = 20.0

    m1_bodies_enabled: bool = True
    m1_body_bars_min: int = 6
    m1_body_window: int = 10
    m1_body_ratio: float = 0.30

    m1_impulse_enabled: bool = True

    bb_enabled: bool = False
    bb_period: int = 20
    bb_std: float = 2.0
    bb_min_bandwidth: float = 0.0015

    m1_entry_enabled: bool = True
    m1_touch_max_atr: float = 1.0
    m1_close_ema_eps_atr: float = 0.01

    room_tp_enabled: bool = True
    room_tp_min_atr: float = 1.5

    revision: int = 1

    def condition_enabled(self, key: str) -> bool:
        return bool(getattr(self, f"{key}_enabled", True))

    def toggle_condition(self, key: str) -> None:
        if key not in CONDITION_ORDER:
            raise ValueError(f"unknown condition: {key}")
        attr = f"{key}_enabled"
        setattr(self, attr, not getattr(self, attr))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> ScalpStrategyParams:
        if not raw:
            return cls()
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in raw.items() if k in known}
        p = cls()
        for k, v in data.items():
            setattr(p, k, v)
        return p

    def fingerprint(self) -> str:
        d = self.to_dict()
        d.pop("revision", None)
        blob = json.dumps(d, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def apply_patch(self, patch: dict[str, Any]) -> list[str]:
        """Применить изменения; вернуть список изменённых ключей."""
        known = {f.name for f in fields(cls)}
        changed: list[str] = []
        for k, v in patch.items():
            if k not in known:
                continue
            old = getattr(self, k)
            if k.endswith("_enabled"):
                v = _parse_bool(v)
            elif isinstance(old, int):
                v = int(v)
            elif isinstance(old, float):
                v = float(v)
            if old != v:
                setattr(self, k, v)
                changed.append(k)
        if changed:
            self.revision += 1
        return changed

    def debug_header_lines(self, *, symbol: str, task_id: int | None) -> list[str]:
        tid = f"#{task_id}" if task_id else "?"
        lines = [
            "=" * 60,
            f"SCALP STRATEGY CONFIG | {symbol} {tid} | rev={self.revision} fp={self.fingerprint()}",
            "=" * 60,
            f"EMA {self.ema_fast}/{self.ema_slow} · ATR {self.atr_period} · ADX {self.adx_period} · SL {self.sl_atr_mult}×ATR",
            "",
        ]
        for key in CONDITION_ORDER:
            en = self.condition_enabled(key)
            mark = "ON " if en else "OFF"
            lines.append(f"  [{mark}] {CONDITION_LABELS[key]}")
            lines.extend(self._condition_params_lines(key))
        lines.append("=" * 60)
        lines.append("")
        return lines

    def _condition_params_lines(self, key: str) -> list[str]:
        ind = "      "
        if key == "m5_cross":
            return [f"{ind}lookback={self.m5_cross_lookback} M5 св"]
        if key == "m5_pullback":
            return [f"{ind}max={self.m5_pullback_max_atr} ATR"]
        if key == "m1_adx":
            return [f"{ind}min ADX={self.m1_adx_min}"]
        if key == "m1_bodies":
            return [
                f"{ind}window={self.m1_body_window} св · "
                f"≥{self.m1_body_bars_min} тел >{self.m1_body_ratio:.0%} диапазона"
            ]
        if key == "bb":
            return [
                f"{ind}period={self.bb_period} std={self.bb_std} · "
                f"min bandwidth={self.bb_min_bandwidth} ({self.bb_min_bandwidth * 100:.2f}%)"
            ]
        if key == "m1_entry":
            return [
                f"{ind}touch ≤{self.m1_touch_max_atr} ATR · "
                f"close eps={self.m1_close_ema_eps_atr} ATR"
            ]
        if key == "room_tp":
            return [f"{ind}min room={self.room_tp_min_atr} ATR до TP1"]
        return []

    def format_telegram(self) -> str:
        lines = [
            f"rev {self.revision} · fp <code>{self.fingerprint()}</code>",
            "⇄ вкл/выкл · ✏️ Параметры — выбор блока и ввод числа",
            "",
        ]
        for key in CONDITION_ORDER:
            lines.extend(self._condition_telegram_lines(key))
            lines.append("")
        lines.append(
            f"<b>Общие</b> EMA {self.ema_fast}/{self.ema_slow} · "
            f"ATR {self.atr_period} · ADX {self.adx_period} · SL {self.sl_atr_mult}×ATR"
        )
        return "\n".join(lines).rstrip()

    def _condition_telegram_lines(self, key: str) -> list[str]:
        en = "🟢" if self.condition_enabled(key) else "⚪"
        out = [f"{en} {CONDITION_LABELS[key]}"]
        for pl in self._condition_params_lines(key):
            out.append(pl.replace("      ", "   "))
        if key == "m5_trend":
            out.append("   <i>Только вкл/выкл (чисел нет).</i>")
        elif key == "m1_impulse":
            out.append("   <i>Только вкл/выкл (3 close по правилу).</i>")
        help_text = CONDITION_HELP.get(key, "")
        if help_text:
            out.append(f"   <i>{help_text}</i>")
        return out

    def format_field_value(self, field: str) -> str:
        cur = getattr(self, field, None)
        if isinstance(cur, float) and field == "m1_body_ratio":
            return f"{cur:g} ({cur:.0%})"
        if isinstance(cur, float):
            return f"{cur:g}"
        return str(cur)

    def field_prompt(self, field: str, *, task_label: str = "") -> str:
        label = FIELD_LABELS.get(field, field)
        cond_key = FIELD_CONDITION.get(field, "")
        if cond_key == "global":
            cond_line = "<b>Блок:</b> общие EMA / ATR / SL"
        elif cond_key:
            cond_line = f"<b>Условие:</b> {CONDITION_LABELS[cond_key]}"
        else:
            cond_line = ""
        cur_s = self.format_field_value(field)
        lines = []
        if task_label:
            lines.append(f"<b>{task_label}</b>")
        lines.append(f"<b>Параметр:</b> {label}")
        if cond_line:
            lines.append(cond_line)
        lines.append(f"<b>Сейчас:</b> <code>{cur_s}</code>")
        lines.append("")
        hint = FIELD_INPUT_HINT.get(field)
        if hint:
            lines.append(f"<b>Что ввести:</b> {hint}")
        effect = FIELD_EFFECT.get(field)
        if effect:
            lines.append(f"<b>На что влияет:</b> {effect}")
        lines.append("")
        lines.append("Отправьте одно новое число. Отмена — /cancel")
        return "\n".join(lines)

    def edit_group_text(self, group: str) -> str:
        title = "Общие EMA / ATR / SL" if group == "global" else CONDITION_LABELS.get(group, group)
        lines = [
            f"<b>Блок:</b> {title}",
            "Выберите параметр — откроется форма ввода с пояснением.",
            "",
        ]
        for field in EDIT_GROUPS.get(group, ()):
            lines.append(
                f"• <b>{FIELD_LABELS.get(field, field)}</b>: "
                f"<code>{self.format_field_value(field)}</code>"
            )
            effect = FIELD_EFFECT.get(field, "")
            if effect:
                lines.append(f"  <i>{effect}</i>")
            lines.append("")
        return "\n".join(lines).rstrip()


def default_scalp_strategy() -> ScalpStrategyParams:
    return ScalpStrategyParams()


def _parse_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "on", "да")


def parse_field_value(field: str, text: str) -> Any:
    """Разбор одного значения для поля стратегии."""
    v = text.strip().replace(",", ".")
    if field.endswith("_enabled"):
        return _parse_bool(v)
    int_fields = {
        "ema_fast",
        "ema_slow",
        "atr_period",
        "adx_period",
        "m5_cross_lookback",
        "m1_body_bars_min",
        "m1_body_window",
        "bb_period",
    }
    if field in int_fields:
        val = int(float(v))
        if val <= 0:
            raise ValueError("Число должно быть > 0")
        return val
    val = float(v)
    if field == "m1_body_ratio" and not (0 < val <= 1):
        raise ValueError("Доля тела: от 0 до 1 (напр. 0.3)")
    if val <= 0 and field not in ("m1_close_ema_eps_atr", "bb_min_bandwidth"):
        raise ValueError("Число должно быть > 0")
    return val


