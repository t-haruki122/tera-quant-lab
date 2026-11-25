"""Shared validators for API input."""

import re
from calendar import monthrange
from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, Field


def normalize_symbol(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9^][A-Za-z0-9.^=\-]{0,19}", value):
        raise ValueError("symbol は英数字または ^ . = - で20文字以内にしてください")
    return value.upper()


def require_iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("日付は YYYY-MM-DD 形式で指定してください")
    return value


def shift_years(day: date, years: int) -> date:
    year = day.year + years
    if year < 1:
        return date.min
    if year > 9999:
        return date.max
    return day.replace(year=year, day=min(day.day, monthrange(year, day.month)[1]))


Symbol = Annotated[str, AfterValidator(normalize_symbol)]
ISODate = Annotated[date, BeforeValidator(require_iso_date)]
HistoryInterval = Literal["1d", "1wk", "1mo"]
SQLiteID = Annotated[int, Field(gt=0, le=2**63 - 1)]
