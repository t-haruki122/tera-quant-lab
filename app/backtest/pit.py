"""Annual EPS is usable at fiscal period end + reporting lag, never today's PER."""

from math import isfinite

import pandas as pd


def number(value):
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if isfinite(result) else None


def annual_eps(payload, lag_days=90):
    if not isinstance(lag_days, int) or lag_days < 0:
        raise ValueError("eps_lag_days must be a nonnegative integer")
    entries = []
    for row in payload.get("history", []):
        # Old servers only supplied a year; conservatively assume December 31.
        period = row.get("period_end") or row.get("period")
        if period is None:
            continue
        period = str(period)
        if len(period) == 4 and period.isdigit():
            period += "-12-31"
        end = pd.to_datetime(period, errors="coerce", utc=True)
        if pd.isna(end):
            continue
        eps = number(row.get("eps"))
        if eps is None:
            income, shares = number(row.get("net_income")), number(row.get("shares"))
            if income is not None and shares is not None and shares > 0:
                eps = income / shares
        # Keep missing EPS too: an unusable newer fiscal year must not revive older EPS.
        entries.append((end.tz_convert(None) + pd.Timedelta(days=lag_days), eps))
    return sorted(entries, key=lambda item: item[0])


def eps_for_bars(dates, payload, lag_days=90):
    entries = annual_eps(payload, lag_days)
    values = []
    for date in pd.to_datetime(dates, errors="coerce", utc=True):
        latest = None
        if not pd.isna(date):
            for available, eps in entries:
                if available > date.tz_convert(None):
                    break
                latest = eps
        values.append(latest)
    return pd.Series(values, index=dates.index, dtype=float)
