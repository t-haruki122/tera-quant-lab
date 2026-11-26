from datetime import date
from itertools import groupby
from statistics import fmean, pstdev

import pandas as pd
import pytest

from app.services import stock_service
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


def reference_ema(values, period):
    seed = fmean(values[:period])
    alpha = 2 / (period + 1)
    decay = 1 - alpha
    return [
        seed * decay**i + alpha * sum(value * decay**age for age, value in enumerate(reversed(values[period:period + i])))
        for i in range(len(values) - period + 1)
    ]


@pytest.mark.parametrize("interval", ["1d", "1wk"])
@pytest.mark.parametrize("use_start", [True, False])
def test_history_macd_and_bollinger_against_reference(client, monkeypatch, interval, use_start):
    start, end = "2024-01-02", "2024-02-15"
    fetched = []

    def history(symbol, from_date, to_date, source):
        assert symbol == "TEST" and source == "1d" and to_date == end
        assert (from_date is not None and from_date < start) if use_start else from_date is None
        days = pd.date_range(from_date or "2023-05-01", end, freq="B")
        for i, day in enumerate(days):
            close = 100 + 0.13 * i + 0.7 * (i % 13) + 0.4 * (i % 7)
            fetched.append(record(day.date().isoformat(), close - 1, close + 2, close - 2, close, 100))
        return fetched.copy()

    monkeypatch.setattr(stock_service._client, "get_history", history)
    url = f"/stock/TEST/history?end_date={end}&interval={interval}"
    response = client.get(f"{url}&start_date={start}" if use_start else url)
    assert response.status_code == 200, response.text

    def week(row):
        return date.fromisoformat(row["date"]).isocalendar()[:2]

    bars = fetched if interval == "1d" else [list(rows)[-1] for _, rows in groupby(fetched, week)]
    closes = [row["close"] for row in bars]
    fast, slow = reference_ema(closes, 12), reference_ema(closes, 26)
    macd = [short - long for short, long in zip(fast[14:], slow, strict=True)]
    signal = reference_ema(macd, 9)
    expected_bars = [row for row in bars if not use_start or row["date"] >= start]
    result = response.json()["history"]
    assert result and [row["date"] for row in result] == [row["date"] for row in expected_bars]
    offset = len(bars) - len(result)

    for i, row in enumerate(result, offset):
        window = closes[max(0, i - 19):i + 1]
        middle = fmean(window)
        sigma = pstdev(window)
        expected = {
            "bb_middle": middle if i >= 19 else None,
            "bb_upper": middle + 2 * sigma if i >= 19 else None,
            "bb_lower": middle - 2 * sigma if i >= 19 else None,
            "macd": macd[i - 25] if i >= 25 else None,
            "macd_signal": signal[i - 33] if i >= 33 else None,
            "macd_hist": macd[i - 25] - signal[i - 33] if i >= 33 else None,
        }
        for field, value in expected.items():
            assert row[field] == (round(value, 4) if value is not None else None)
    assert all(result[0][field] is not None for field in expected) == use_start
