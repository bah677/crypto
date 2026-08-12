from __future__ import annotations

from dataclasses import dataclass

from app.advisor.intervals import validate_kline_interval


@dataclass(frozen=True)
class AdvisorTask:
    """Одно задание советчика: пара, ТФ, EMA, опционально торговые часы (МСК)."""

    symbol: str
    interval: str
    ema_fast: int
    ema_slow: int
    trading_hours: list[dict[str, str]]
    db_id: int | None = None
    enabled: bool = False
    bybit_category: str = "linear"
    alias: str = ""

    @property
    def key(self) -> str:
        return (
            f"{self.symbol}|{self.bybit_category}|{self.interval}|"
            f"{self.ema_fast}|{self.ema_slow}"
        )

    @property
    def interval_label(self) -> str:
        if not self.interval.isdigit():
            return self.interval
        mins = int(self.interval)
        if mins % 60 == 0 and mins >= 60:
            h = mins // 60
            return f"{h}ч" if h < 24 else f"{mins}m"
        return f"{mins}m"

    def signal_interval_label(self) -> str:
        """Формат в сигнале: 5m, 1h, 4h, D."""
        if not self.interval.isdigit():
            return self.interval
        mins = int(self.interval)
        if mins % 60 == 0 and mins >= 60:
            return f"{mins // 60}h"
        return f"{mins}m"

    def signal_title(self, *, emoji: str, side_ru: str) -> str:
        tf = self.signal_interval_label()
        alias = self.alias.strip()
        if alias:
            return f"{emoji} {side_ru} · {alias} ({self.symbol}) · {tf}"
        return f"{emoji} {side_ru} · {self.symbol} · {tf}"

    @property
    def display_name(self) -> str:
        alias = self.alias.strip()
        if alias:
            return f"{alias} ({self.symbol})"
        return self.symbol

    def format_signal_message(
        self,
        *,
        emoji: str,
        side_ru: str,
        bar_label: str,
        extra_lines: list[str] | None = None,
    ) -> str:
        lines = [
            self.signal_title(emoji=emoji, side_ru=side_ru),
            f"EMA {self.ema_fast}/{self.ema_slow} · свеча {bar_label}",
        ]
        if extra_lines:
            lines.extend(extra_lines)
        return "\n".join(lines)

    def hours_label(self) -> str:
        from app.trading_schedule import format_schedule_label

        return format_schedule_label(self.trading_hours)


def parse_trading_hours_field(raw: str) -> list[dict[str, str]]:
    from app.trading_schedule import parse_schedule_field

    return parse_schedule_field(raw)  # type: ignore[return-value]


def _parse_task_line(line: str, default_hours: list[dict[str, str]]) -> AdvisorTask:
    line = line.strip()
    if not line or line.startswith("#"):
        raise ValueError("пустая строка")
    parts = [p.strip() for p in line.split(";")]
    if len(parts) < 4:
        raise ValueError(
            "ожидается SYMBOL;INTERVAL;EMA_FAST;EMA_SLOW[;HOURS], "
            f"получено {len(parts)} полей: {line!r}"
        )
    symbol = parts[0].upper()
    interval = validate_kline_interval(parts[1])
    ema_fast = int(parts[2])
    ema_slow = int(parts[3])
    if ema_fast <= 0 or ema_slow <= 0:
        raise ValueError("EMA должны быть > 0")
    if ema_fast == ema_slow:
        raise ValueError("EMA быстрая и медленная не должны совпадать")
    hours_raw = parts[4] if len(parts) > 4 else ""
    hours = parse_trading_hours_field(hours_raw) if hours_raw else list(default_hours)
    return AdvisorTask(
        symbol=symbol,
        interval=interval,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        trading_hours=hours,
    )


def parse_advisor_tasks(raw: str, default_trading_hours: str = "") -> list[AdvisorTask]:
    """
    ADVISOR_TASKS: задания через «|», поля через «;».
    Пример: XAUTUSDT;5;9;21|BTCUSDT;15;12;26;09:00-18:00
    """
    default_hours = parse_trading_hours_field(default_trading_hours)
    text = (raw or "").strip()
    if not text:
        return []
    tasks: list[AdvisorTask] = []
    seen: set[str] = set()
    for chunk in text.replace("\n", "|").split("|"):
        chunk = chunk.strip()
        if not chunk or chunk.startswith("#"):
            continue
        task = _parse_task_line(chunk, default_hours)
        if task.key in seen:
            raise ValueError(f"дубликат задания: {task.key}")
        seen.add(task.key)
        tasks.append(task)
    return tasks


def advisor_task_from_row(row) -> AdvisorTask:
    from app.db.models import AdvisorTaskRow

    if not isinstance(row, AdvisorTaskRow):
        raise TypeError("ожидается AdvisorTaskRow")
    return AdvisorTask(
        symbol=row.symbol,
        interval=row.kline_interval,
        ema_fast=row.ema_fast,
        ema_slow=row.ema_slow,
        trading_hours=row.trading_hours(),
        db_id=row.id,
        enabled=row.enabled,
        bybit_category=row.bybit_category or "linear",
        alias=(row.alias or "").strip(),
    )
