from __future__ import annotations

ALLOWED_KLINE_INTERVALS = frozenset({"5", "15", "30", "60"})
_KLINE_ORDER = ("5", "15", "30", "60")


def validate_kline_interval(interval: str) -> str:
    iv = str(interval).strip()
    if iv not in ALLOWED_KLINE_INTERVALS:
        raise ValueError(
            "Допустимые интервалы советчика (минуты): 5, 15, 30, 60"
        )
    return iv


def lower_kline_interval(interval: str) -> str | None:
    """Младший стандартный ТФ; для 5m — None (берём предыдущую свечу того же ТФ)."""
    iv = validate_kline_interval(interval)
    idx = _KLINE_ORDER.index(iv)
    if idx == 0:
        return None
    return _KLINE_ORDER[idx - 1]


def higher_span_minutes(interval: str) -> int:
    """Длина синтетической старшей свечи, заканчивающейся в момент закрытия сигнальной."""
    iv = validate_kline_interval(interval)
    mins = int(iv)
    if mins == 5:
        return 15
    if mins == 60:
        return 120
    return mins * 2


def higher_aggregate_ratio(interval: str) -> int:
    return higher_span_minutes(interval) // int(validate_kline_interval(interval))


def junior_interval_label(interval: str) -> str:
    """Подпись младшего ТФ (МТФ) для отчётов."""
    lower = lower_kline_interval(interval)
    if lower is None:
        return "пред. 5m"
    mins = int(lower)
    if mins >= 60 and mins % 60 == 0:
        return f"{mins // 60}h"
    return f"{mins}m"


def senior_interval_label(interval: str) -> str:
    """Подпись старшего ТФ (СТФ), синтетика."""
    span = higher_span_minutes(interval)
    if span >= 60 and span % 60 == 0:
        return f"{span // 60}h синт."
    return f"{span}m синт."


def parse_advisor_ema_block(text: str) -> tuple[int, int, str]:
    raw = text.strip()
    if "," in raw:
        raise ValueError("EMA / интервал: десятичный разделитель только точка «.».")
    parts = raw.split()
    if len(parts) != 3:
        raise ValueError(
            "Нужно ровно три значения через пробел: "
            "<EMA быстрая> <EMA медленная> <интервал минут>"
        )
    fast, slow = int(parts[0]), int(parts[1])
    interval = validate_kline_interval(parts[2])
    if fast <= 0 or slow <= 0:
        raise ValueError("Периоды EMA должны быть положительными.")
    return fast, slow, interval
