"""UTC clocks and the single SQLite datetime conversion boundary."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_db(value: datetime | None = None) -> datetime:
    """Store naive UTC; existing naive database values already represent UTC."""
    value = utcnow() if value is None else value
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value


def utc_iso(value: datetime | None = None) -> str:
    """Preserve the API's naive-ISO-plus-Z wire format."""
    return to_db(value).isoformat() + "Z"
