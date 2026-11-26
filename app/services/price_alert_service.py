"""Manage price alert rules and evaluate them against current quotes."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.db_models import PriceAlert
from app.schemas.investments import AlertCheckResponse, AlertResponse, AlertWrite, FiredAlert, SkippedAlert
from app.services.investment_quotes import fetch_quotes, finite, validate_symbol
from app.services.user_service import lock_user_data
from app.timeutil import to_db


async def alerts_for_user(user_id: int, db: AsyncSession, *, enabled_only: bool = False) -> list[PriceAlert]:
    query = select(PriceAlert).where(PriceAlert.user_id == user_id).order_by(PriceAlert.id)
    if enabled_only:
        query = query.where(PriceAlert.enabled.is_(True))
    return list(await db.scalars(query))


async def require_alert_space(user_id: int, db: AsyncSession) -> None:
    maximum = get_settings().price_alert_max_per_user
    if len(await alerts_for_user(user_id, db)) >= maximum:
        raise ValueError(f"価格アラートは {maximum} 件まで登録できます。")


async def add_alert(user_id: int, data: AlertWrite, db: AsyncSession) -> PriceAlert:
    await require_alert_space(user_id, db)
    await validate_symbol(data.symbol, db)
    await lock_user_data(user_id, db)
    await require_alert_space(user_id, db)
    alert = PriceAlert(user_id=user_id, **data.model_dump())
    db.add(alert)
    await db.commit()
    return alert


async def get_alert(user_id: int, alert_id: int, db: AsyncSession) -> PriceAlert:
    alert = await db.scalar(select(PriceAlert).where(PriceAlert.user_id == user_id, PriceAlert.id == alert_id))
    if alert is None:
        raise LookupError("価格アラートが見つかりません。")
    return alert


async def update_alert(user_id: int, alert_id: int, data: AlertWrite, db: AsyncSession) -> PriceAlert:
    await lock_user_data(user_id, db)
    alert = await get_alert(user_id, alert_id, db)
    if data.symbol != alert.symbol:
        await validate_symbol(data.symbol, db)
        await lock_user_data(user_id, db)
        alert = await get_alert(user_id, alert_id, db)
        if data.symbol != alert.symbol:
            alert.last_triggered_market_date = None
    if (alert.symbol, alert.kind, alert.threshold) != (data.symbol, data.kind, data.threshold):
        alert.armed = True
    for field, value in data.model_dump().items():
        setattr(alert, field, value)
    await db.commit()
    return alert


async def delete_alert(user_id: int, alert_id: int, db: AsyncSession) -> None:
    await lock_user_data(user_id, db)
    await db.delete(await get_alert(user_id, alert_id, db))
    await db.commit()


def observed_value(alert: AlertResponse, quote: dict | None) -> float | SkippedAlert:
    if quote is None:
        reason = "株価を取得できませんでした。"
    elif quote.get("stale"):
        return SkippedAlert(
            id=alert.id, symbol=alert.symbol, reason="古いキャッシュのため判定を見送りました。",
            stale=True, cached_at=quote.get("cached_at"),
        )
    elif alert.kind == "change_pct":
        change = finite(quote.get("day_change_pct"))
        if change is not None:
            return abs(change)
        reason = "当日の騰落率を取得できませんでした。"
    else:
        price = finite(quote.get("price"))
        if price is not None and price >= 0:
            return price
        reason = "株価を取得できませんでした。"
    return SkippedAlert(id=alert.id, symbol=alert.symbol, reason=reason)


def quote_session_date(quote: dict | None) -> date | None:
    if not quote or not (market_time := quote.get("market_time")):
        return None
    try:
        market_date = quote.get("market_date")
        if market_date:
            return date.fromisoformat(market_date)
        timestamp = datetime.fromisoformat(market_time)
        return (timestamp.astimezone(UTC) if timestamp.tzinfo else timestamp).date()
    except (TypeError, ValueError, OverflowError):
        return None


async def check_alerts(user_id: int, db: AsyncSession) -> AlertCheckResponse:
    snapshot = {row.id: AlertResponse.model_validate(row) for row in await alerts_for_user(
        user_id, db, enabled_only=True,
    )}
    response = AlertCheckResponse(fired=[], skipped=[])
    if not snapshot:
        return response

    bind = db.bind
    await db.rollback()
    settings = get_settings()
    quotes = await fetch_quotes({alert.symbol for alert in snapshot.values()}, bind,
                                settings.watchlist_summary_concurrency)
    cooldown = timedelta(minutes=settings.alert_cooldown_minutes)

    # Serialize transitions with edits, imports, and checks from other tabs.
    await lock_user_data(user_id, db)
    now = to_db()
    for current in await alerts_for_user(user_id, db, enabled_only=True):
        original = snapshot.get(current.id)
        if original is None or original != AlertResponse.model_validate(current):
            continue
        quote = quotes[current.symbol]
        observed = observed_value(original, quote)
        if isinstance(observed, SkippedAlert):
            response.skipped.append(observed)
            continue
        matches = observed <= current.threshold if current.kind == "below" else observed >= current.threshold
        if not matches:
            current.armed = True
            continue
        if current.kind == "change_pct":
            market_date = quote_session_date(quote)
            session_date = market_date or now.date()
            if current.last_triggered_market_date:
                if session_date <= current.last_triggered_market_date:
                    continue
                if not market_date and current.last_triggered_at and current.last_triggered_at.date() == now.date():
                    continue
            current.last_triggered_market_date = session_date
        elif not current.armed or (current.last_triggered_at and now - current.last_triggered_at < cooldown):
            continue
        current.last_triggered_at = now
        current.trigger_count = min(current.trigger_count + 1, 2**63 - 1)
        current.armed = current.kind == "change_pct"
        response.fired.append(FiredAlert(
            **AlertResponse.model_validate(current).model_dump(), observed_value=observed,
        ))
    await db.commit()
    return response
