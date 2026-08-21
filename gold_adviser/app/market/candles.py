from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Candle:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def open_time_key(self) -> str:
        dt = self.open_time
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip().replace("Z", "+00:00")
    # Twelve Data often: "2024-01-01 12:00:00"
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def median_step_seconds(candles: list[Candle]) -> float | None:
    """Медианный шаг open_time между соседними свечами (сек)."""
    if len(candles) < 2:
        return None
    steps = [
        (candles[i + 1].open_time - candles[i].open_time).total_seconds()
        for i in range(len(candles) - 1)
    ]
    steps = [s for s in steps if s > 0]
    if not steps:
        return None
    steps.sort()
    return steps[len(steps) // 2]


def assert_m1_spacing(candles: list[Candle], *, provider: str) -> None:
    """
    RealMarket FREE иногда отдаёт M5 под timeFrame=M1.
    Отклоняем серию, если медианный шаг далеко от 60с.
    """
    step = median_step_seconds(candles)
    if step is None:
        return
    if abs(step - 60.0) > 15.0:
        raise RuntimeError(
            f"{provider}: ожидался M1 (~60с), получен шаг ~{step:.0f}с "
            f"({len(candles)} св)"
        )


def only_closed_m1(
    candles: list[Candle],
    *,
    now: datetime | None = None,
    bar_seconds: float = 60.0,
) -> list[Candle]:
    """Оставляет только полностью закрытые M1 (open + 60с <= now)."""
    if not candles:
        return []
    ts = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    out: list[Candle] = []
    for c in candles:
        ot = c.open_time
        if ot.tzinfo is None:
            ot = ot.replace(tzinfo=timezone.utc)
        if ot.timestamp() + bar_seconds <= ts.timestamp():
            out.append(c)
    return out
