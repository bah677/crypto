"""Wilder ATR и ADX."""

from __future__ import annotations


def true_range(h: float, l: float, prev_close: float) -> float:
    return max(h - l, abs(h - prev_close), abs(l - prev_close))


def atr_wilder(
    bars: list[tuple[int, float, float, float, float]],
    period: int = 14,
) -> list[float | None]:
    n = len(bars)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    trs: list[float] = []
    for i in range(1, n):
        _, _, h, l, _ = bars[i]
        pc = bars[i - 1][4]
        trs.append(true_range(h, l, pc))
    if len(trs) < period:
        return out
    prev = sum(trs[:period]) / period
    out[period] = prev
    for i in range(period, len(trs)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i + 1] = prev
    return out


def adx_wilder(
    bars: list[tuple[int, float, float, float, float]],
    period: int = 14,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    n = len(bars)
    plus_di: list[float | None] = [None] * n
    minus_di: list[float | None] = [None] * n
    adx: list[float | None] = [None] * n
    if n <= period + 1:
        return plus_di, minus_di, adx

    tr_list: list[float] = []
    pdm_list: list[float] = []
    mdm_list: list[float] = []
    for i in range(1, n):
        _, _, h, l, _ = bars[i]
        ph, pl = bars[i - 1][2], bars[i - 1][3]
        pc = bars[i - 1][4]
        up = h - ph
        down = pl - l
        pdm_list.append(up if up > down and up > 0 else 0.0)
        mdm_list.append(down if down > up and down > 0 else 0.0)
        tr_list.append(true_range(h, l, pc))

    def _wilder_series(vals: list[float]) -> list[float]:
        if len(vals) < period:
            return []
        s = sum(vals[:period]) / period
        out_s = [s]
        for v in vals[period:]:
            s = (s * (period - 1) + v) / period
            out_s.append(s)
        return out_s

    tr_s = _wilder_series(tr_list)
    pdm_s = _wilder_series(pdm_list)
    mdm_s = _wilder_series(mdm_list)
    dx_vals: list[float] = []
    for tr_v, p, m in zip(tr_s, pdm_s, mdm_s):
        if tr_v <= 0:
            dx_vals.append(0.0)
            continue
        pdi = 100 * p / tr_v
        mdi = 100 * m / tr_v
        s = pdi + mdi
        dx_vals.append(100 * abs(pdi - mdi) / s if s > 0 else 0.0)

    adx_s = _wilder_series(dx_vals)
    base = period
    for j, tr_v in enumerate(tr_s):
        idx = j + 1
        if idx >= n:
            break
        pdi = 100 * pdm_s[j] / tr_v if tr_v > 0 else 0
        mdi = 100 * mdm_s[j] / tr_v if tr_v > 0 else 0
        plus_di[idx] = pdi
        minus_di[idx] = mdi
    for k, adx_v in enumerate(adx_s):
        idx = base + k
        if idx < n:
            adx[idx] = adx_v
    return plus_di, minus_di, adx
