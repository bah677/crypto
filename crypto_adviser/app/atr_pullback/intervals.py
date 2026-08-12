from __future__ import annotations

ATR_BTF_INTERVALS = frozenset({"5", "15", "30", "60"})
ATR_MTF_INTERVALS = frozenset({"1", "5", "15", "30", "60"})
ATR_KLINE_ORDER = ("1", "5", "15", "30", "60")


def validate_btf_interval(interval: str) -> str:
    iv = str(interval).strip()
    if iv not in ATR_BTF_INTERVALS:
        raise ValueError("БТФ: допустимые интервалы (минуты): 5, 15, 30, 60")
    return iv


def validate_mtf_interval(interval: str) -> str:
    iv = str(interval).strip()
    if iv not in ATR_MTF_INTERVALS:
        raise ValueError("МТФ: допустимые интервалы (минуты): 1, 5, 15, 30, 60")
    return iv


def validate_btf_mtf(btf: str, mtf: str) -> tuple[str, str]:
    b = validate_btf_interval(btf)
    m = validate_mtf_interval(mtf)
    if ATR_KLINE_ORDER.index(b) <= ATR_KLINE_ORDER.index(m):
        raise ValueError(
            f"БТФ ({b}m) должен быть старше МТФ ({m}m). "
            "МТФ: 1, 5, 15, 30; БТФ: 5, 15, 30, 60"
        )
    return b, m


def lower_intervals_for_btf(btf: str) -> list[str]:
    b = validate_btf_interval(btf)
    idx = ATR_KLINE_ORDER.index(b)
    return list(ATR_KLINE_ORDER[:idx])


def interval_label(iv: str) -> str:
    mins = int(iv)
    if mins >= 60 and mins % 60 == 0:
        return f"{mins // 60}h"
    return f"{mins}m"


def allowed_btf_intervals() -> list[str]:
    return [x for x in ATR_KLINE_ORDER if x in ATR_BTF_INTERVALS]
