"""Read watchlist market data and value holdings in yen."""

import asyncio
import logging
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import PROVIDER_FAILURES, CacheWindow
from app.config import get_settings
from app.exceptions import SymbolNotFoundError
from app.market_indicators import BY_SYMBOL, USD_JPY_SYMBOL, is_non_company
from app.models.db_models import CompanyProfile, Indicator, StockMemo, StockPrice, UserListItem
from app.services import indicator_service, investment_quotes, profile_service, stock_service, user_service
from app.services.investment_quotes import Bind, finite
from app.validation import currency_code

logger = logging.getLogger(__name__)
PROFILE_FIELDS = ("name", "country", "sector", "currency", "market_cap")
INDICATOR_FIELDS = ("mix_index", "dividend_yield", "per", "pbr", "roe")


async def get_list_memos(user_id: int, list_id: int, db: AsyncSession) -> dict[str, str | None]:
    rows = await db.execute(
        select(UserListItem.symbol, StockMemo.memo)
        .join(StockMemo, func.upper(UserListItem.symbol) == func.upper(StockMemo.symbol))
        .where(StockMemo.user_id == user_id, UserListItem.list_id == list_id)
        # Prefer an exact match when a legacy case variant also exists.
        .order_by(StockMemo.symbol == UserListItem.symbol, StockMemo.id)
    )
    return {symbol: memo for symbol, memo in rows}


def snapshot_fields(symbol: str, quote, profile, indicators, *, cached: bool = False) -> dict:
    fields: dict = {"symbol": symbol}
    if (indicator := BY_SYMBOL.get(symbol)) and is_non_company(symbol):
        profile = {"name": indicator.name, "currency": indicator.currency}
        indicators = {}
    if profile is not None:
        fields.update({key: profile.get(key) for key in PROFILE_FIELDS})
        fields["currency"] = currency_code(fields["currency"])
    if quote is not None:
        fields.update(price=quote.get("price"), currency=currency_code(quote.get("currency")) or fields.get("currency"))
        fields["name"] = fields.get("name") or quote.get("name")
    if indicators is not None:
        fields.update({key: indicators.get(key) for key in INDICATOR_FIELDS})
    sources = (quote, profile, indicators)
    stale_times = [source["cached_at"] for source in sources if source and source.get("stale")]
    if stale_times:
        fields.update(stale=True, cached_at=min(stale_times))
    if any(source is None for source in sources):
        if cached:
            fields["pending"] = True
        else:
            fields["error"] = (
                "銘柄データを取得できませんでした。"
                if quote is profile is indicators is None
                else "一部のデータを取得できませんでした。"
            )
    return {key: finite(value) if isinstance(value, float) else value for key, value in fields.items()}


async def get_snapshot(symbol: str, bind: Bind) -> dict:
    async def lookup(service):
        try:
            async with AsyncSession(bind=bind) as db:
                return await service(symbol, db)
        except (*PROVIDER_FAILURES, SymbolNotFoundError):
            return None
        except Exception:
            logger.exception("Watchlist lookup failed for %s", symbol)
            return None

    sources = await asyncio.gather(
        lookup(stock_service.get_current_price), lookup(profile_service.get_profile),
        lookup(indicator_service.get_indicators),
    )
    return snapshot_fields(symbol, *sources)


async def cached_snapshots(list_id: int, db: AsyncSession) -> list[dict]:
    # Look up all three cache entries in one query, including data past the refresh TTL.
    quote_id = stock_service.quote_query(UserListItem.symbol).with_only_columns(StockPrice.id).correlate(UserListItem)
    rows = await db.execute(
        select(UserListItem.symbol, StockPrice, CompanyProfile, Indicator)
        .outerjoin(StockPrice, StockPrice.id == quote_id.scalar_subquery())
        .outerjoin(CompanyProfile, CompanyProfile.symbol == UserListItem.symbol)
        .outerjoin(Indicator, Indicator.symbol == UserListItem.symbol)
        .where(UserListItem.list_id == list_id)
        .order_by(UserListItem.id)
    )
    settings = get_settings()
    quote_window = CacheWindow(settings.cache_ttl_current_price)
    detail_window = CacheWindow(settings.cache_ttl_history)
    snapshots = []
    for symbol, q, p, i in rows:
        quote = {**stock_service.cached_quote(q), **quote_window.metadata(q.cached_at)} if q else None
        if symbol == USD_JPY_SYMBOL:
            quote = await stock_service.read_cached_usd_jpy_quote(db) or quote
        snapshots.append(snapshot_fields(
            symbol, quote,
            {**{key: getattr(p, key) for key in PROFILE_FIELDS}, **detail_window.metadata(p.cached_at)} if p else None,
            {**indicator_service.cached_indicators(i), **detail_window.metadata(i.cached_at)} if i else None,
            cached=True,
        ))
    return snapshots


def percentage(value: float, total: float) -> float | None:
    return value / total * 100 if total else None


def value_holdings(items: list[dict], cash: int | None, fx: dict) -> dict | None:
    holdings = [item for item in items if item["shares"] is not None]
    if not holdings and not cash:
        return None
    cash = cash or 0
    rate = finite(fx.get("rate"))
    rate = rate if rate and rate > 0 else None
    regions = dict.fromkeys(("日本株", "米国株", "その他", "現金"), 0.0)
    regions["現金"] = float(cash)
    currencies = {"JPY": float(cash), "USD": 0.0}
    sectors: dict[str, float] = defaultdict(float)
    excluded = []
    valued: list[dict] = []
    stocks = 0.0
    for item in holdings:
        price = finite(item.get("price"))
        currency = currency_code(item.get("currency"))
        warning = None if currency in currencies else (
            f"{currency} 建ての円換算には対応していません。" if currency else "取引通貨を取得できません。"
        )
        if price is None or price < 0:
            reason = "株価を取得できません。"
        elif currency == "USD" and rate is None:
            reason = "USD/JPY レートを取得できません。"
        elif currency in ("JPY", "USD"):
            value = finite(item["shares"] * price * ((rate or 1) if currency == "USD" else 1))
            if value is not None and finite(stocks + cash + value) is not None:
                item["value_jpy"] = value
                stocks += value
                regions["日本株" if currency == "JPY" else "米国株"] += value
                currencies[currency] += value
                sectors[item.get("sector") or "不明"] += value
                valued.append(item)
                continue
            reason = "評価額を計算できません。"
        else:
            reason = warning
        item["warning"] = warning or reason
        excluded.append({"symbol": item["symbol"], "reason": reason})

    valued.sort(key=lambda item: (-item["value_jpy"], item["symbol"]))
    total = stocks + cash
    for item in valued:
        item["weight_pct"] = percentage(item["value_jpy"], total)

    def weights(groups, denominator):
        return [
            {"label": label, "value_jpy": value, "pct": percentage(value, denominator)}
            for label, value in groups.items()
        ]

    return {
        "total_value_jpy": total,
        "stocks_value_jpy": stocks,
        "cash_jpy": cash,
        "cash_ratio_pct": percentage(cash, total),
        "by_region": weights(regions, total),
        "by_currency": weights(currencies, total),
        "by_sector": weights(dict(sorted(sectors.items(), key=lambda pair: (-pair[1], pair[0]))), stocks),
        "concentration": {
            "largest": {"symbol": valued[0]["symbol"], "pct": valued[0]["weight_pct"]} if valued and total else None,
            "top_5_pct": percentage(sum(item["value_jpy"] for item in valued[:5]), total) if valued else None,
            "holding_count": len(holdings),
        },
        "usd_jpy": {"rate": rate, "stale": bool(fx.get("stale")), "cached_at": fx.get("cached_at")} if rate else None,
        "excluded": excluded,
    }


async def get_list_summary(user_id: int, list_id: int, db: AsyncSession, concurrency: int, *, cached: bool) -> dict:
    data = await user_service.get_list_detail(user_id, list_id, db)
    if not data["items"]:
        data["portfolio"] = value_holdings([], data["cash_jpy"], {})
        return data
    memos = await get_list_memos(user_id, list_id, db)
    bind = db.bind
    if cached:
        snapshots = await cached_snapshots(list_id, db)
    else:
        # Release the read connection before opening the cache writers.
        await db.rollback()
        slots = asyncio.Semaphore(concurrency)

        async def summarize(symbol):
            async with slots:
                return await get_snapshot(symbol, bind)

        snapshots = await asyncio.gather(*(summarize(item["symbol"]) for item in data["items"]))

    snapshot_by_symbol = {snapshot["symbol"]: snapshot for snapshot in snapshots}
    items = [{**item, **snapshot_by_symbol.get(item["symbol"], {}), "memo": memos.get(item["symbol"])}
             for item in data["items"]]
    fx = {}
    if any(item["shares"] is not None and currency_code(item.get("currency")) == "USD" for item in items):
        if cached:
            fx = await investment_quotes.cached_usd_jpy(db)
        else:
            fx = await investment_quotes.fetch_usd_jpy(bind)
            if fx and not fx.get("cached_at"):
                fx["cached_at"] = (await investment_quotes.cached_usd_jpy(db)).get("cached_at")
    data["items"] = items
    data["portfolio"] = value_holdings(items, data["cash_jpy"], fx)
    return data
