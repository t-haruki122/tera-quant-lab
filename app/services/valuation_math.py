"""Shared fiscal data selection and valuation ratios."""

import re
from collections.abc import Iterable
from math import isfinite

from sqlalchemy import or_

from app.models.db_models import JQFinSummary


def is_japanese_stock(symbol: str) -> bool:
    return re.fullmatch(r"[A-Z0-9]{4}\.T", symbol) is not None


def finite(value: float | None) -> float | None:
    return value if value is not None and isfinite(value) else None


def positive(value: float | None) -> float | None:
    return value if finite(value) is not None and value is not None and value > 0 else None


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not (divisor := positive(denominator)):
        return None
    return finite(numerator / divisor)


def percent(value: float | None) -> float | None:
    return finite(value * 100) if value is not None else None


def valuation(price: float | None, eps: float | None, bps: float | None) -> dict[str, float | None]:
    per = ratio(positive(price), eps)
    pbr = ratio(positive(price), bps)
    return {"per": per, "pbr": pbr, "mix_index": finite(per * pbr) if per and pbr else None}


def jq_roe(row: JQFinSummary) -> float | None:
    reported = finite(row.roe)
    if reported is not None and abs(reported) <= 10:
        return percent(reported)
    return percent(finite(row.net_profit / row.equity)) if row.net_profit is not None and row.equity else None


def jq_actuals():
    """Forecast-only revisions have no actual statement amounts."""
    return or_(*(column.is_not(None) for column in (
        JQFinSummary.eps, JQFinSummary.bps, JQFinSummary.sales, JQFinSummary.operating_profit,
        JQFinSummary.net_profit, JQFinSummary.equity, JQFinSummary.equity_ratio, JQFinSummary.roe,
    )))


def jq_annual_rows(rows: Iterable[JQFinSummary]) -> list[JQFinSummary]:
    """Choose one annual statement per period, preferring consolidated actuals."""
    annual = sorted(
        (row for row in rows if row.period_type == "FY" and (row.period_end or row.fy_end)),
        key=lambda row: (row.period_end or row.fy_end, row.consolidated is True,
                         row.disclosed_date, row.disclosed_time is not None, row.disclosed_time, row.disc_no),
        reverse=True,
    )
    by_period = {}
    for row in annual:
        by_period.setdefault(row.period_end or row.fy_end, row)
    return list(by_period.values())
