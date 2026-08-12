"""Торговое расписание по МСК: время и опционально дни недели."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")

_TIME_RANGE_RE = re.compile(
    r"^(?:(?P<days>.+?)\s+)?(?P<start>\d{1,2}:\d{2})\s*-\s*(?P<end>\d{1,2}:\d{2})$",
    re.IGNORECASE,
)

_DAY_TOKEN: dict[str, int] = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
    "пн": 0,
    "пон": 0,
    "понедельник": 0,
    "вт": 1,
    "вторник": 1,
    "ср": 2,
    "среда": 2,
    "чт": 3,
    "четверг": 3,
    "пт": 4,
    "пятница": 4,
    "сб": 5,
    "суббота": 5,
    "вс": 6,
    "воскресенье": 6,
}

_DAY_SHORT_RU = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _parse_hhmm(s: str) -> tuple[int, int]:
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Неверное время: {s}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Неверное время: {s}")
    return h, m


def _time_to_sec(h: int, m: int, sec: int = 0) -> int:
    return h * 3600 + m * 60 + sec


def _token_to_weekday(token: str) -> int:
    key = token.strip().lower().replace(".", "")
    if key.isdigit():
        iso = int(key)
        if not 1 <= iso <= 7:
            raise ValueError(f"День недели 1–7 (Пн–Вс), получено: {token!r}")
        return iso - 1
    if key not in _DAY_TOKEN:
        raise ValueError(f"Неизвестный день: {token!r}")
    return _DAY_TOKEN[key]


def _expand_day_range(start_t: str, end_t: str) -> list[int]:
    a = _token_to_weekday(start_t)
    b = _token_to_weekday(end_t)
    if a <= b:
        return list(range(a, b + 1))
    return list(range(a, 7)) + list(range(0, b + 1))


def parse_days_spec(spec: str) -> list[int]:
    """Разбор префикса дней: Mon-Fri, 1-5, пн,ср,пт."""
    spec = spec.strip()
    if not spec:
        raise ValueError("Пустое указание дней")
    if "-" in spec and "," not in spec:
        a, b = spec.split("-", 1)
        return _expand_day_range(a.strip(), b.strip())
    days: set[int] = set()
    for part in spec.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            for d in _expand_day_range(*part.split("-", 1)):
                days.add(d)
        else:
            days.add(_token_to_weekday(part))
    if not days:
        raise ValueError(f"Не удалось разобрать дни: {spec!r}")
    return sorted(days)


def parse_schedule_line(line: str) -> dict[str, str | list[int]]:
    line = line.strip()
    if not line:
        raise ValueError("пустая строка")
    m = _TIME_RANGE_RE.match(line)
    if not m:
        raise ValueError(
            f"Ожидается «[дни ]ЧЧ:ММ-ЧЧ:ММ», например Пн-Пт 09:00-18:00 или 09:00-18:00: {line!r}"
        )
    start, end = m.group("start"), m.group("end")
    _parse_hhmm(start)
    _parse_hhmm(end)
    window: dict[str, str | list[int]] = {"start": start, "end": end}
    days_raw = m.group("days")
    if days_raw:
        window["days"] = parse_days_spec(days_raw)
    return window


def parse_schedule_text(text: str) -> list[dict[str, str | list[int]]]:
    """Несколько строк — несколько окон. Пустой текст / «-» = круглосуточно (пустой список)."""
    raw = (text or "").strip()
    if not raw or raw.lower() in ("24/7", "круглосуточно", "all", "-", "—"):
        return []
    windows: list[dict[str, str | list[int]]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        windows.append(parse_schedule_line(line))
    if not windows:
        raise ValueError("Укажите хотя бы одно окно, например:\nПн-Пт 09:00-18:00")
    return windows


def parse_schedule_field(raw: str) -> list[dict[str, str | list[int]]]:
    """Legacy: одна строка с запятыми между окнами (без дней)."""
    raw = (raw or "").strip()
    if not raw or raw.lower() in ("24/7", "круглосуточно", "all", "-", "—"):
        return []
    if "\n" in raw:
        return parse_schedule_text(raw)
    windows: list[dict[str, str | list[int]]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if _TIME_RANGE_RE.match(part):
            windows.append(parse_schedule_line(part))
        else:
            if "-" not in part:
                raise ValueError(f"Окно должно быть start-end, получено: {part!r}")
            start, end = part.split("-", 1)
            _parse_hhmm(start.strip())
            _parse_hhmm(end.strip())
            windows.append({"start": start.strip(), "end": end.strip()})
    return windows


def _window_days(window: dict) -> list[int] | None:
    raw = window.get("days")
    if raw is None:
        return None
    if isinstance(raw, list):
        return [int(x) for x in raw]
    if isinstance(raw, str):
        return parse_days_spec(raw)
    raise ValueError(f"Некорректное поле days: {raw!r}")


def _time_in_window(now: datetime, start: str, end: str) -> bool:
    cur = _time_to_sec(now.hour, now.minute, now.second)
    sh, sm = _parse_hhmm(start)
    eh, em = _parse_hhmm(end)
    a = _time_to_sec(sh, sm)
    b = _time_to_sec(eh, em)
    if a <= b:
        return a <= cur <= b
    return cur >= a or cur <= b


def now_msk_in_windows(windows: list[dict]) -> bool:
    """True если сейчас (МСК) попадает в одно из окон. Пустой список = всегда True."""
    return msk_datetime_in_windows(windows, datetime.now(MSK))


def msk_datetime_in_windows(windows: list[dict], moment: datetime) -> bool:
    """Проверка расписания для произвольного момента (МСК)."""
    if not windows:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=MSK)
    else:
        moment = moment.astimezone(MSK)
    wd = moment.weekday()
    for w in windows:
        days = _window_days(w)
        if days is not None and wd not in days:
            continue
        if _time_in_window(moment, w["start"], w["end"]):
            return True
    return False


def _format_days(days: list[int]) -> str:
    if sorted(days) == list(range(7)):
        return ""
    if days == list(range(5)):
        return "Пн–Пт"
    if days == [5, 6]:
        return "Сб–Вс"
    if len(days) == 1:
        return _DAY_SHORT_RU[days[0]]
    return ",".join(_DAY_SHORT_RU[d] for d in days)


def format_schedule_window(window: dict) -> str:
    start, end = window["start"], window["end"]
    days = _window_days(window)
    if days is None:
        return f"{start}–{end}"
    prefix = _format_days(days)
    return f"{prefix} {start}–{end}".strip()


def format_schedule_label(windows: list[dict]) -> str:
    if not windows:
        return "круглосуточно (МСК)"
    return "; ".join(format_schedule_window(w) for w in windows)


SCHEDULE_HELP = (
    "По одному окну на строку: <b>[дни ]ЧЧ:ММ-ЧЧ:ММ</b> (МСК).\n"
    "Дни необязательны — без них окно каждый день.\n\n"
    "Дни: <code>Пн-Пт</code>, <code>Сб-Вс</code>, <code>1-5</code> (1=Пн … 7=Вс), "
    "<code>пн,ср,пт</code>, <code>Mon-Fri</code>\n\n"
    "Примеры:\n"
    "• <code>Пн-Пт 09:00-18:00</code>\n"
    "• <code>Сб-Вс 10:00-16:00</code>\n"
    "• <code>09:00-18:00</code> — каждый день\n"
    "• <code>-</code> — круглосуточно"
)
