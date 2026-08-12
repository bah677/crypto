from app.indicators.atr import robust_atr


def _bar(h: float, l: float) -> tuple:
    return (0, 0.0, h, l, (h + l) / 2)


def test_robust_atr_excludes_outliers():
    bars = [_bar(10, 9) for _ in range(29)] + [_bar(100, 0)]  # один выброс
    atr = robust_atr(bars, window=30)
    assert atr is not None
    assert atr < 5  # выброс 100 не должен доминировать
