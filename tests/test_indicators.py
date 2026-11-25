import pytest

from app.services.stock_service import _aggregate_history, _calculate_rsi, _calculate_sma


@pytest.mark.parametrize("closes,period,expected", [
    ([], 3, []), ([1, 2], 3, [None, None]), ([1, 2, 6, 4], 3, [None, None, 3, 4]),
    ([3, 5], 1, [3, 5]),
])
def test_sma(closes, period, expected):
    assert _calculate_sma(closes, period) == expected


@pytest.mark.parametrize("closes,expected", [
    ([], []), ([1, 2], [None, None]),
    ([1, 2, 3, 4], [None, None, 100, 100]),
    ([4, 3, 2, 1], [None, None, 0, 0]),
    ([2, 2, 2], [None, None, 100]),
    # Initial average gain/loss = 1 and 0.5 -> RSI=200/3.
    # Next loss=1: Wilder averages = 0.5 and 0.75 -> RSI=40.
    ([1, 3, 2, 1], [None, None, 200 / 3, 40]),
])
def test_rsi_wilder_smoothing(closes, expected):
    actual = _calculate_rsi(closes, period=2)
    assert len(actual) == len(expected)
    for value, target in zip(actual, expected, strict=True):
        assert value is None if target is None else value == pytest.approx(target)


def record(day, open_, high, low, close, volume):
    return dict(date=day, open=open_, high=high, low=low, close=close, volume=volume)


@pytest.mark.parametrize("interval,dates,next_date", [
    ("1wk", ["2024-01-01", "2024-01-03", "2024-01-07"], "2024-01-08"),
    ("1mo", ["2024-01-01", "2024-01-15", "2024-01-31"], "2024-02-01"),
    ("1wk", ["2023-12-31", "2023-12-29", "2023-12-30"], "2024-01-01"),
])
def test_weekly_monthly_ohlcv(interval, dates, next_date):
    dates = sorted(dates)
    rows = [record(dates[0], 10, 15, 8, 12, 100), record(dates[1], 12, 20, 9, 11, 200),
            record(dates[2], 11, 14, 7, 13, 300), record(next_date, 13, 16, 12, 15, 400)]
    assert _aggregate_history(list(reversed(rows)), interval) == [
        record(dates[-1], 10, 20, 7, 13, 600), rows[-1],
    ]


@pytest.mark.parametrize("interval", ["1wk", "1mo"])
def test_aggregation_empty_and_missing_fields(interval):
    assert _aggregate_history([], interval) == []
    row = record("2024-01-01", None, None, None, 10, None)
    assert _aggregate_history([row], interval) == [row]
    zero_volume = record("2024-01-02", 10, 11, 9, 10, 0)
    assert _aggregate_history([row, zero_volume], interval) == [record("2024-01-02", None, 11, 9, 10, 0)]


def test_daily_aggregation_is_passthrough():
    rows = [record("2024-01-01", 1, 3, 0, 2, 5)]
    assert _aggregate_history(rows, "1d") is rows
