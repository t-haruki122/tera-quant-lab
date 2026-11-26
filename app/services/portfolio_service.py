"""Manage the user's holdings and value them with cached market data."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.db_models import CompanyProfile, PortfolioHolding
from app.schemas.investments import (
    HoldingCreate,
    HoldingResponse,
    HoldingUpdate,
    HoldingValuation,
    PortfolioResponse,
    PortfolioTotals,
)
from app.services.investment_quotes import fetch_quotes, fetch_usd_jpy, finite, validate_symbol
from app.services.user_service import lock_user_data


class DuplicateHolding(ValueError):
    pass


async def holdings_for_user(user_id: int, db: AsyncSession) -> list[PortfolioHolding]:
    return list(await db.scalars(
        select(PortfolioHolding).where(PortfolioHolding.user_id == user_id).order_by(PortfolioHolding.id)
    ))


def require_unique_holding(holdings: list[PortfolioHolding], symbol: str, holding_id: int | None = None) -> None:
    if any(holding.symbol == symbol and holding.id != holding_id for holding in holdings):
        raise DuplicateHolding("この銘柄は登録済みです。既存の保有銘柄を編集してください。")


async def require_holding_space(user_id: int, symbol: str, db: AsyncSession) -> None:
    holdings = await holdings_for_user(user_id, db)
    require_unique_holding(holdings, symbol)
    maximum = get_settings().portfolio_max_holdings
    if len(holdings) >= maximum:
        raise ValueError(f"保有銘柄は {maximum} 件まで登録できます。")


async def add_holding(user_id: int, data: HoldingCreate, db: AsyncSession) -> PortfolioHolding:
    await require_holding_space(user_id, data.symbol, db)
    await validate_symbol(data.symbol, db)
    await lock_user_data(user_id, db)
    await require_holding_space(user_id, data.symbol, db)
    holding = PortfolioHolding(user_id=user_id, **data.model_dump())
    db.add(holding)
    await db.commit()
    return holding


async def get_holding(user_id: int, holding_id: int, db: AsyncSession) -> PortfolioHolding:
    holding = await db.scalar(select(PortfolioHolding).where(
        PortfolioHolding.user_id == user_id, PortfolioHolding.id == holding_id,
    ))
    if holding is None:
        raise LookupError("保有銘柄が見つかりません。")
    return holding


async def update_holding(
    user_id: int, holding_id: int, data: HoldingUpdate, db: AsyncSession,
) -> PortfolioHolding:
    await lock_user_data(user_id, db)
    holding = await get_holding(user_id, holding_id, db)
    if data.symbol is not None and data.symbol != holding.symbol:
        require_unique_holding(await holdings_for_user(user_id, db), data.symbol, holding_id)
        await validate_symbol(data.symbol, db)
        await lock_user_data(user_id, db)
        holding = await get_holding(user_id, holding_id, db)
        require_unique_holding(await holdings_for_user(user_id, db), data.symbol, holding_id)
        holding.symbol = data.symbol
    for field, value in data.model_dump(exclude={"symbol"}).items():
        setattr(holding, field, value)
    await db.commit()
    return holding


async def delete_holding(user_id: int, holding_id: int, db: AsyncSession) -> None:
    await lock_user_data(user_id, db)
    await db.delete(await get_holding(user_id, holding_id, db))
    await db.commit()


def value_holding(
    holding: HoldingResponse, quote: dict, name: str | None, currency: str | None, rate: float | None,
) -> HoldingValuation:
    item = HoldingValuation(
        **holding.model_dump(), name=name, currency=currency,
        stale=quote.get("stale"), cached_at=quote.get("cached_at"),
        day_change=finite(quote.get("day_change")), day_change_pct=finite(quote.get("day_change_pct")),
        fx_unavailable=rate is None,
    )
    price = finite(quote.get("price"))
    item.cost_basis = finite(holding.shares * holding.avg_cost)
    if price is None or price < 0:
        item.error = "株価を取得できませんでした。"
        return item
    item.price = price
    item.market_value = finite(price * holding.shares)
    if item.market_value is None or item.cost_basis is None:
        item.error = "評価額または取得額が計算可能な範囲を超えています。"
        return item
    item.unrealized_pl = item.market_value - item.cost_basis
    item.unrealized_pl_pct = finite(item.unrealized_pl / item.cost_basis * 100) if item.cost_basis else None
    if rate is None:
        item.error = "円換算に必要な通貨または為替レートを取得できませんでした。"
        return item
    market = finite(item.market_value * rate)
    cost = finite(item.cost_basis * rate)
    if market is None or cost is None:
        item.error = "円換算額が計算可能な範囲を超えています。"
        return item
    item.market_value_jpy = market
    item.cost_basis_jpy = cost
    item.unrealized_pl_jpy = market - cost
    return item


async def summarize(user_id: int, db: AsyncSession) -> PortfolioResponse:
    holdings = [HoldingResponse.model_validate(row) for row in await holdings_for_user(user_id, db)]
    symbols = {holding.symbol for holding in holdings}
    profiles = {
        symbol: (name, currency)
        for symbol, name, currency in await db.execute(
            select(CompanyProfile.symbol, CompanyProfile.name, CompanyProfile.currency)
            .where(CompanyProfile.symbol.in_(symbols))
        )
    } if symbols else {}
    bind = db.bind
    await db.rollback()
    quotes = await fetch_quotes(symbols, bind, get_settings().watchlist_summary_concurrency)

    def metadata(symbol: str) -> tuple[str | None, str | None]:
        quote = quotes[symbol] or {}
        name, currency = profiles.get(symbol, (None, None))
        return quote.get("name") or name, quote.get("currency") or currency

    instruments = {symbol: metadata(symbol) for symbol in symbols}
    forex = await fetch_usd_jpy(bind) if any(currency == "USD" for _, currency in instruments.values()) else {}
    usd_jpy = finite(forex.get("rate"))
    if usd_jpy is not None and usd_jpy <= 0:
        usd_jpy = None
    rates = {"JPY": 1.0, "USD": usd_jpy}
    items = []
    for holding in holdings:
        name, currency = instruments[holding.symbol]
        item = value_holding(holding, quotes[holding.symbol] or {}, name, currency, rates.get(currency or ""))
        items.append(item)

    market = finite(sum(item.market_value_jpy or 0.0 for item in items))
    cost = finite(sum(item.cost_basis_jpy or 0.0 for item in items))
    pl = market - cost if market is not None and cost is not None else None
    for item in items:
        if market and item.market_value_jpy is not None:
            item.weight_pct = item.market_value_jpy / market * 100
    sources = [*filter(None, quotes.values()), forex]
    stale_times = [source["cached_at"] for source in sources if source.get("stale") and source.get("cached_at")]
    return PortfolioResponse(
        holdings=items, totals=PortfolioTotals(
            market_value_jpy=market, cost_basis_jpy=cost, unrealized_pl_jpy=pl,
            unrealized_pl_pct=finite(pl / cost * 100) if pl is not None and cost else None,
        ),
        partial=market is None or cost is None or any(item.error for item in items),
        stale=any(source.get("stale") for source in sources),
        cached_at=min(stale_times, default=None),
        usd_jpy=usd_jpy, forex_stale=bool(forex.get("stale")), forex_cached_at=forex.get("cached_at"),
    )
