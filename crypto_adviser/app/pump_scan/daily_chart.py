"""Дневной и недельный график: свечи + EMA для pump-алерта."""

from __future__ import annotations

import io
from datetime import datetime
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from app.bybit.rest import BybitRest, _interval_to_ms
from app.indicators.ema import ema_series

MSK = ZoneInfo("Europe/Moscow")
CHART_DAYS = 90
CHART_M5_BARS = 90
FETCH_LIMIT = 250
MIN_CHART_BARS = 2
_IMPULSE_LABEL = "цена алерта"
_CANDLE_WIDTH = 0.6
_THEME = {
    "fig": "#1a1a2e",
    "ax": "#16213e",
    "grid": "#888888",
    "spine": "#444444",
    "tick": "#b0b0b0",
    "title": "#e8e8e8",
    "legend_fc": "#1a1a2e",
    "legend_ec": "#444",
    "legend_lc": "#ddd",
    "up": "#26a69a",
    "down": "#ef5350",
    "ema50": "#ff9800",
    "ema100": "#42a5f5",
    "ema200": "#ab47bc",
    "ema7w": "#ffb74d",
    "ema14w": "#64b5f6",
    "ema28w": "#ce93d8",
    "impulse": "#ffeb3b",
}
CHART_WEEKS = 52


def _align_ema_to_bars(
    series: list[float | None], n_bars: int, n_closed: int
) -> list[float | None]:
    """EMA по закрытым дням; на формирующейся свече — последнее известное значение."""
    out: list[float | None] = [None] * n_bars
    for i in range(min(n_closed, len(series))):
        out[i] = series[i]
    last: float | None = None
    for v in series:
        if v is not None:
            last = v
    if last is not None and n_closed < n_bars:
        for i in range(n_closed, n_bars):
            out[i] = last
    return out


def _daily_index_for_ts(daily_bars: list[tuple], ts_ms: int) -> int | None:
    idx: int | None = None
    for i, bar in enumerate(daily_bars):
        if bar[0] <= ts_ms:
            idx = i
        else:
            break
    return idx


def _daily_emas_on_intraday(
    daily_bars: list[tuple],
    intraday_bars: list[tuple],
    *,
    closed_count: int,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """EMA 50/100/200 с дневки, привязанные к каждой внутридневной свече."""
    closes_all = [b[4] for b in daily_bars[:closed_count]]
    e50 = _align_ema_to_bars(ema_series(closes_all, 50), len(daily_bars), closed_count)
    e100 = _align_ema_to_bars(ema_series(closes_all, 100), len(daily_bars), closed_count)
    e200 = _align_ema_to_bars(ema_series(closes_all, 200), len(daily_bars), closed_count)

    out50: list[float | None] = []
    out100: list[float | None] = []
    out200: list[float | None] = []
    for bar in intraday_bars:
        idx = _daily_index_for_ts(daily_bars, bar[0])
        if idx is None:
            out50.append(None)
            out100.append(None)
            out200.append(None)
        else:
            out50.append(e50[idx])
            out100.append(e100[idx])
            out200.append(e200[idx])
    return out50, out100, out200


def _fetch_kline_bars(
    client: BybitRest,
    symbol: str,
    interval: str,
    *,
    limit: int,
    as_of_ms: int | None = None,
) -> list[tuple[int, float, float, float, float]]:
    import time

    raw = client.get_kline_ohlcv(symbol, interval, limit=limit, end_ms=as_of_ms)
    if not raw:
        return []
    raw.sort(key=lambda x: x[0])
    step = _interval_to_ms(interval)
    now_ms = as_of_ms if as_of_ms is not None else int(time.time() * 1000)
    closed = [b for b in raw if b[0] + step <= now_ms]
    in_progress = [b for b in raw if b[0] <= now_ms < b[0] + step]
    bars = list(closed)
    if in_progress:
        bars.append(in_progress[-1])
    return bars


def _candle_width_days(dates: list[datetime], *, fraction: float = 0.72) -> float:
    """Ширина тела свечи по шагу оси времени (для 5m — доли дня, не 0.6)."""
    if len(dates) < 2:
        return 5 / (24 * 60) * fraction
    nums = [mdates.date2num(d) for d in dates]
    spacings = [nums[i + 1] - nums[i] for i in range(len(nums) - 1) if nums[i + 1] > nums[i]]
    if not spacings:
        return 5 / (24 * 60) * fraction
    spacings.sort()
    step = spacings[len(spacings) // 2]
    return max(step * fraction, step * 0.3)


def _chart_span_label(bars: list[tuple], interval: str) -> str:
    n = len(bars)
    if n < 2:
        return f"{n} св"
    step_ms = _interval_to_ms(interval)
    hours = (bars[-1][0] - bars[0][0] + step_ms) / 3_600_000
    if interval == "5":
        return f"{n}×5m · {hours:.1f}ч"
    if interval == "D":
        return f"{n}d"
    if interval == "W":
        return f"{n}w"
    return f"{n} св"


def _plot_candles(
    ax,
    bars: list[tuple],
    dates: list[datetime],
    *,
    width: float = _CANDLE_WIDTH,
) -> None:
    for i, bar in enumerate(bars):
        _, o, h, l, c = bar[0], bar[1], bar[2], bar[3], bar[4]
        x = mdates.date2num(dates[i])
        up = c >= o
        color = _THEME["up"] if up else _THEME["down"]
        ax.plot([x, x], [l, h], color=color, linewidth=0.8, solid_capstyle="round")
        body_bottom = min(o, c)
        body_h = max(abs(c - o), (h - l) * 0.02 if h != l else o * 0.001)
        rect = Rectangle(
            (x - width / 2, body_bottom),
            width,
            body_h,
            facecolor=color,
            edgecolor=color,
        )
        ax.add_patch(rect)


def _plot_ema_lines(
    ax,
    dates: list[datetime],
    values: list[float | None],
    *,
    color: str,
    label: str,
) -> None:
    xs: list[float] = []
    ys: list[float] = []
    for i, v in enumerate(values):
        if v is not None:
            xs.append(mdates.date2num(dates[i]))
            ys.append(v)
    if xs:
        ax.plot(xs, ys, color=color, linewidth=1.4, label=label, alpha=0.95)


def _style_axes(ax, *, title: str) -> None:
    ax.set_title(title, color=_THEME["title"], fontsize=12, pad=10)
    ax.tick_params(colors=_THEME["tick"], labelsize=8)
    ax.grid(True, alpha=0.15, color=_THEME["grid"])
    ax.legend(
        loc="upper left",
        fontsize=8,
        facecolor=_THEME["legend_fc"],
        edgecolor=_THEME["legend_ec"],
        labelcolor=_THEME["legend_lc"],
    )
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M", tz=MSK))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=8))
    for spine in ax.spines.values():
        spine.set_color(_THEME["spine"])


def _fig_to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _fetch_daily_bars(
    client: BybitRest,
    symbol: str,
    *,
    as_of_ms: int | None = None,
) -> list[tuple[int, float, float, float, float]]:
    import time

    raw = client.get_kline_ohlcv(symbol, "D", limit=FETCH_LIMIT, end_ms=as_of_ms)
    if not raw:
        return []
    raw.sort(key=lambda x: x[0])
    step = _interval_to_ms("D")
    now_ms = as_of_ms if as_of_ms is not None else int(time.time() * 1000)
    closed = [b for b in raw if b[0] + step <= now_ms]
    in_progress = [b for b in raw if b[0] <= now_ms < b[0] + step]
    bars = list(closed)
    if in_progress:
        bars.append(in_progress[-1])
    return bars


def render_daily_chart_png(
    client: BybitRest,
    symbol: str,
    *,
    days: int = CHART_DAYS,
    as_of_ms: int | None = None,
    impulse_price: float | None = None,
) -> bytes | None:
    """PNG: до `days` дневок (если истории меньше — вся доступная) + EMA 50/100/200."""
    bars = _fetch_daily_bars(client, symbol, as_of_ms=as_of_ms)
    if len(bars) < MIN_CHART_BARS:
        return None

    display = bars[-min(days, len(bars)) :]
    step = _interval_to_ms("D")
    now_ms = as_of_ms if as_of_ms is not None else int(__import__("time").time() * 1000)
    closed_count = sum(1 for b in bars if b[0] + step <= now_ms)
    closes_all = [b[4] for b in bars[:closed_count]]

    e50 = ema_series(closes_all, 50)
    e100 = ema_series(closes_all, 100)
    e200 = ema_series(closes_all, 200)

    n_bars = len(bars)
    ema50_all = _align_ema_to_bars(e50, n_bars, closed_count)
    ema100_all = _align_ema_to_bars(e100, n_bars, closed_count)
    ema200_all = _align_ema_to_bars(e200, n_bars, closed_count)
    ema50_d = ema50_all[-len(display) :]
    ema100_d = ema100_all[-len(display) :]
    ema200_d = ema200_all[-len(display) :]

    dates = [datetime.fromtimestamp(b[0] / 1000, MSK) for b in display]

    fig, ax = plt.subplots(figsize=(10, 5), dpi=120)
    fig.patch.set_facecolor(_THEME["fig"])
    ax.set_facecolor(_THEME["ax"])

    _plot_candles(ax, display, dates, width=_candle_width_days(dates))
    _plot_ema_lines(ax, dates, ema50_d, color=_THEME["ema50"], label="EMA50 1D")
    _plot_ema_lines(ax, dates, ema100_d, color=_THEME["ema100"], label="EMA100 1D")
    _plot_ema_lines(ax, dates, ema200_d, color=_THEME["ema200"], label="EMA200 1D")

    if impulse_price is not None and impulse_price > 0:
        ax.axhline(
            impulse_price,
            color=_THEME["impulse"],
            linestyle="--",
            linewidth=1.0,
            alpha=0.85,
            label=_IMPULSE_LABEL,
        )

    pair = symbol.upper()
    n = len(display)
    hist_note = " · вся история" if n < days else ""
    _style_axes(ax, title=f"{pair} · 1D · {n}d{hist_note}")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m", tz=MSK))
    fig.autofmt_xdate(rotation=30)

    return _fig_to_png(fig)


def _fetch_weekly_bars(
    client: BybitRest,
    symbol: str,
    *,
    as_of_ms: int | None = None,
) -> list[tuple[int, float, float, float, float]]:
    import time

    raw = client.get_kline_ohlcv(symbol, "W", limit=FETCH_LIMIT, end_ms=as_of_ms)
    if not raw:
        return []
    raw.sort(key=lambda x: x[0])
    step = _interval_to_ms("W")
    now_ms = as_of_ms if as_of_ms is not None else int(time.time() * 1000)
    closed = [b for b in raw if b[0] + step <= now_ms]
    in_progress = [b for b in raw if b[0] <= now_ms < b[0] + step]
    bars = list(closed)
    if in_progress:
        bars.append(in_progress[-1])
    return bars


def render_weekly_chart_png(
    client: BybitRest,
    symbol: str,
    *,
    weeks: int = CHART_WEEKS,
    as_of_ms: int | None = None,
    impulse_price: float | None = None,
) -> bytes | None:
    """PNG: недельные свечи + EMA 7/14/28."""
    bars = _fetch_weekly_bars(client, symbol, as_of_ms=as_of_ms)
    if len(bars) < MIN_CHART_BARS:
        return None

    display = bars[-min(weeks, len(bars)) :]
    step = _interval_to_ms("W")
    now_ms = as_of_ms if as_of_ms is not None else int(__import__("time").time() * 1000)
    closed_count = sum(1 for b in bars if b[0] + step <= now_ms)
    closes_all = [b[4] for b in bars[:closed_count]]

    e7 = ema_series(closes_all, 7)
    e14 = ema_series(closes_all, 14)
    e28 = ema_series(closes_all, 28)

    n_bars = len(bars)
    ema7_all = _align_ema_to_bars(e7, n_bars, closed_count)
    ema14_all = _align_ema_to_bars(e14, n_bars, closed_count)
    ema28_all = _align_ema_to_bars(e28, n_bars, closed_count)
    ema7_w = ema7_all[-len(display) :]
    ema14_w = ema14_all[-len(display) :]
    ema28_w = ema28_all[-len(display) :]

    dates = [datetime.fromtimestamp(b[0] / 1000, MSK) for b in display]

    fig, ax = plt.subplots(figsize=(10, 4.5), dpi=120)
    fig.patch.set_facecolor(_THEME["fig"])
    ax.set_facecolor(_THEME["ax"])

    _plot_candles(ax, display, dates, width=_candle_width_days(dates))
    _plot_ema_lines(ax, dates, ema7_w, color=_THEME["ema7w"], label="EMA7 1W")
    _plot_ema_lines(ax, dates, ema14_w, color=_THEME["ema14w"], label="EMA14 1W")
    _plot_ema_lines(ax, dates, ema28_w, color=_THEME["ema28w"], label="EMA28 1W")

    if impulse_price is not None and impulse_price > 0:
        ax.axhline(
            impulse_price,
            color=_THEME["impulse"],
            linestyle="--",
            linewidth=1.0,
            alpha=0.85,
            label=_IMPULSE_LABEL,
        )

    pair = symbol.upper()
    n = len(display)
    hist_note = " · вся история" if n < weeks else ""
    _style_axes(ax, title=f"{pair} · 1W · {n}w{hist_note}")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m.%y", tz=MSK))
    fig.autofmt_xdate(rotation=30)

    return _fig_to_png(fig)


def render_m5_chart_with_daily_ema_png(
    client: BybitRest,
    symbol: str,
    *,
    bars_count: int = CHART_M5_BARS,
    as_of_ms: int | None = None,
    impulse_price: float | None = None,
) -> bytes | None:
    """PNG: ровно `bars_count` свечей 5m + EMA 50/100/200 с дневки."""
    import time

    step_ms = _interval_to_ms("5")
    now_ms = as_of_ms if as_of_ms is not None else int(time.time() * 1000)
    # Запас на формирующуюся + 90 закрытых
    m5_bars = _fetch_kline_bars(
        client, symbol, "5", limit=bars_count + 10, as_of_ms=as_of_ms
    )
    daily_bars = _fetch_daily_bars(client, symbol, as_of_ms=as_of_ms)
    if len(m5_bars) < MIN_CHART_BARS or len(daily_bars) < MIN_CHART_BARS:
        return None

    closed_m5 = [b for b in m5_bars if b[0] + step_ms <= now_ms]
    if len(closed_m5) >= bars_count:
        display = closed_m5[-bars_count:]
    else:
        display = m5_bars[-bars_count:]
    display = display[-bars_count:]

    day_step = _interval_to_ms("D")
    closed_daily = sum(1 for b in daily_bars if b[0] + day_step <= now_ms)

    ema50, ema100, ema200 = _daily_emas_on_intraday(
        daily_bars, display, closed_count=closed_daily
    )
    dates = [datetime.fromtimestamp(b[0] / 1000, MSK) for b in display]
    candle_w = _candle_width_days(dates)

    fig, ax = plt.subplots(figsize=(10, 4.5), dpi=120)
    fig.patch.set_facecolor(_THEME["fig"])
    ax.set_facecolor(_THEME["ax"])

    _plot_candles(ax, display, dates, width=candle_w)
    _plot_ema_lines(ax, dates, ema50, color=_THEME["ema50"], label="EMA50 1D")
    _plot_ema_lines(ax, dates, ema100, color=_THEME["ema100"], label="EMA100 1D")
    _plot_ema_lines(ax, dates, ema200, color=_THEME["ema200"], label="EMA200 1D")

    if impulse_price is not None and impulse_price > 0:
        ax.axhline(
            impulse_price,
            color=_THEME["impulse"],
            linestyle="--",
            linewidth=1.0,
            alpha=0.85,
            label=_IMPULSE_LABEL,
        )

    pair = symbol.upper()
    span = _chart_span_label(display, "5")
    _style_axes(ax, title=f"{pair} · 5m · {span} · EMA 1D")
    fig.autofmt_xdate(rotation=30)

    return _fig_to_png(fig)


def render_pump_alert_charts(
    client: BybitRest,
    symbol: str,
    *,
    as_of_ms: int | None = None,
    impulse_price: float | None = None,
) -> list[bytes]:
    """Три графика для алерта: 1W, 1D и 5m (на 5m — EMA с дневки)."""
    charts: list[bytes] = []
    w1 = render_weekly_chart_png(
        client, symbol, as_of_ms=as_of_ms, impulse_price=impulse_price
    )
    if w1:
        charts.append(w1)
    d1 = render_daily_chart_png(
        client, symbol, as_of_ms=as_of_ms, impulse_price=impulse_price
    )
    if d1:
        charts.append(d1)
    m5 = render_m5_chart_with_daily_ema_png(
        client, symbol, as_of_ms=as_of_ms, impulse_price=impulse_price
    )
    if m5:
        charts.append(m5)
    return charts


def render_pump_alert_chart_png(
    client: BybitRest,
    symbol: str,
    *,
    as_of_ms: int | None = None,
    impulse_price: float | None = None,
) -> bytes | None:
    """Один PNG: 1W + 1D + 5m (для кнопок под сообщением)."""
    parts = render_pump_alert_charts(
        client, symbol, as_of_ms=as_of_ms, impulse_price=impulse_price
    )
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return _stack_png_vertical(parts)


def _stack_png_vertical(parts: list[bytes]) -> bytes:
    from PIL import Image

    images = [Image.open(io.BytesIO(raw)).convert("RGB") for raw in parts]
    width = max(im.width for im in images)
    height = sum(im.height for im in images)
    canvas = Image.new("RGB", (width, height), (26, 26, 46))
    y = 0
    for im in images:
        x = (width - im.width) // 2
        canvas.paste(im, (x, y))
        y += im.height
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()
