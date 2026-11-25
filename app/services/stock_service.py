"""株価サービス - キャッシュ付き株価データ取得"""

from datetime import date, datetime, timedelta
from weakref import WeakKeyDictionary

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.cache import PROVIDER_FAILURES, CacheWindow, allows_stale, cached_response, stale_fields, upsert_cache
from app.clients.yfinance_client import YFinanceClient
from app.config import get_settings
from app.models.db_models import StockPrice
from app.singleflight import coalesce, fetch
from app.stats import stats
from app.timeutil import to_db, utc_iso, utcnow
from app.validation import shift_years

_client = YFinanceClient()
# A bar cannot represent a holiday or an unfinished trading day. Remember the
# furthest end date fetched for each symbol in the same database cache.
_history_coverage: WeakKeyDictionary[AsyncEngine | AsyncConnection, dict[str, tuple[date, datetime]]] = (
    WeakKeyDictionary()
)


def _calculate_sma(closes: list[float], period: int) -> list[float | None]:
    """単純移動平均 (SMA) を計算"""
    sma = []
    for i in range(len(closes)):
        if i < period - 1:
            sma.append(None)
            continue
        window = closes[i - period + 1 : i + 1]
        sma.append(sum(window) / period)
    return sma


def _calculate_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """相対力指数 (RSI) を計算"""
    rsi = []
    gains = 0.0
    losses = 0.0

    for i in range(len(closes)):
        if i == 0:
            rsi.append(None)
            continue
        
        diff = closes[i] - closes[i - 1]
        if i <= period:
            if diff >= 0:
                gains += diff
            else:
                losses -= diff
            
            if i == period:
                # Wilder smoothing starts from average gains/losses, not sums.
                gains /= period
                losses /= period
                if losses == 0:
                    rsi.append(100.0)
                else:
                    rs = (gains / period) / (losses / period)
                    rsi.append(100.0 - (100.0 / (1.0 + rs)))
            else:
                rsi.append(None)
        else:
            current_gain = diff if diff >= 0 else 0.0
            current_loss = -diff if diff < 0 else 0.0
            gains = (gains * (period - 1) + current_gain) / period
            losses = (losses * (period - 1) + current_loss) / period
            
            if losses == 0:
                rsi.append(100.0)
            else:
                rs = (gains / period) / (losses / period)
                rsi.append(100.0 - (100.0 / (1.0 + rs)))
                
    return rsi


def _aggregate_history(records: list[dict], interval: str) -> list[dict]:
    """日次OHLCVを週次/月次OHLCVへ集計する"""
    if interval not in {"1wk", "1mo"}:
        return records
    if not records:
        return []

    sorted_records = sorted(records, key=lambda x: x["date"])
    grouped: dict[str, list[dict]] = {}

    for rec in sorted_records:
        dt = datetime.strptime(rec["date"], "%Y-%m-%d")
        if interval == "1wk":
            # 週次は週の開始日(月曜)単位でグルーピング
            week_start = dt - timedelta(days=dt.weekday())
            key = week_start.strftime("%Y-%m-%d")
        else:
            key = dt.strftime("%Y-%m")

        grouped.setdefault(key, []).append(rec)

    aggregated: list[dict] = []
    for key in sorted(grouped.keys()):
        bucket = grouped[key]
        first = bucket[0]
        last = bucket[-1]

        highs = [r["high"] for r in bucket if r.get("high") is not None]
        lows = [r["low"] for r in bucket if r.get("low") is not None]
        volumes = [r["volume"] for r in bucket if r.get("volume") is not None]

        aggregated.append(
            {
                "date": last["date"],
                "open": first.get("open"),
                "high": max(highs) if highs else None,
                "low": min(lows) if lows else None,
                "close": last["close"],
                "volume": int(sum(volumes)) if volumes else None,
            }
        )

    return aggregated


@coalesce
async def get_current_price(symbol: str, db: AsyncSession) -> dict:
    """現在の株価を取得（キャッシュ優先）"""
    window = CacheWindow(get_settings().cache_ttl_current_price)
    stmt = (
        select(StockPrice)
        .where(StockPrice.symbol == symbol)
        .where(StockPrice.cached_at >= window.usable_since)
        .where(StockPrice.interval == "quote")
        .order_by(StockPrice.cached_at.desc(), StockPrice.id.desc())
        .limit(1)
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    cached = None
    if record is not None:
        cached = (record.cached_at, {"symbol": record.symbol, "price": record.close, "timestamp": utc_iso(record.timestamp)})
    # Release the read session before the shared refresh opens its write session.
    await db.rollback()
    return await cached_response(f"quote:{symbol}", window, cached, lambda: refresh_quote(symbol, db))


@coalesce
async def refresh_quote(symbol: str, db: AsyncSession) -> dict:
    """Refresh the cache for both foreground requests and the warmer."""
    stats.log_api_call()
    data = await fetch(_client.get_current_price, symbol)

    # キャッシュに保存
    values = dict(
        symbol=symbol,
        interval="quote",
        timestamp=datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00")),
        close=data["price"],
    )
    await upsert_cache(db, StockPrice, values)
    await db.commit()

    return data


def _covers_history(
    rows: list[StockPrice], start: str | None, end: date | None,
    coverage: tuple[date, datetime] | None, cutoff: datetime,
) -> bool:
    if not rows or (start and (rows[0].timestamp - datetime.fromisoformat(start)).days > 7):
        return False
    return (
        end is None or rows[-1].timestamp.date() >= end
        or (coverage is not None and coverage[0] >= end and coverage[1] >= cutoff)
    )


def _history_rows(rows: list[StockPrice]) -> list[dict]:
    return [
        {
            "date": row.timestamp.date().isoformat(),
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
        }
        for row in rows
    ]


@coalesce
async def get_history(
    symbol: str,
    db: AsyncSession,
    start_date: str | None = None,
    end_date: str | None = None,
    interval: str = "1d",
    *,
    warmup_days: int = 0,
) -> dict:
    """
    過去の株価データを取得（キャッシュ優先・指標計算付き）
    """
    symbol = symbol.upper()
    requested_interval = interval.lower()
    source_interval = "1d" if requested_interval in {"1wk", "1mo"} else requested_interval

    # 指標計算用に取得期間を過去へ拡張する
    fetch_start_date = start_date
    return_start_date = start_date
    if start_date:
        start_dt_req = datetime.strptime(start_date, "%Y-%m-%d")
        # Requested bar frequency determines the SMA_200 warmup, even though
        # weekly/monthly prices are fetched and cached as daily bars.
        # Bound the extension to 18 years; actual provider coverage can be shorter.
        lookback_days = {"1d": 365, "1wk": 365 * 5, "1mo": 366 * 18}.get(requested_interval, 365 * 3)
        available_days = (start_dt_req - min(start_dt_req, datetime(1900, 1, 1))).days
        fetch_start_dt = start_dt_req - timedelta(days=min(available_days, max(warmup_days, lookback_days)))
        fetch_start_date = fetch_start_dt.date().isoformat()
        return_start_date = (start_dt_req - timedelta(days=min(available_days, warmup_days))).date().isoformat()

    records: list[dict] = []
    metadata = {}
    window = CacheWindow(get_settings().cache_ttl_history)
    end_day = min(date.fromisoformat(end_date), utcnow().date()) if end_date and source_interval == "1d" else None
    fetch_end_date = end_day.isoformat() if end_day is not None else end_date
    bind = db.bind
    coverage = _history_coverage.setdefault(bind, {}) if bind is not None else {}
    cached_records: list[StockPrice] = []

    if source_interval == "1d":
        stmt = (
            select(StockPrice)
            .where(StockPrice.symbol == symbol, StockPrice.interval == "1d", StockPrice.cached_at >= window.usable_since)
            .order_by(StockPrice.timestamp.asc())
        )
        if fetch_start_date:
            stmt = stmt.where(StockPrice.timestamp >= datetime.fromisoformat(fetch_start_date))
        else:
            # A cache warmed by longer requests can contain more data than the provider default.
            anchor = end_day or utcnow().date()
            years = get_settings().history_max_span_years
            earliest = shift_years(anchor, -years)
            if shift_years(earliest, years) < anchor:
                earliest += timedelta(days=1)
            stmt = stmt.where(StockPrice.timestamp >= datetime.combine(earliest, datetime.min.time()))
        if end_day is not None:
            stmt = stmt.where(StockPrice.timestamp <= datetime.combine(end_day, datetime.min.time()))

        cached_records = list((await db.scalars(stmt)).all())
        fresh = [row for row in cached_records if row.cached_at >= window.fresh_since]
        if _covers_history(
            fresh, fetch_start_date, end_day if warmup_days else None, coverage.get(symbol), window.fresh_since,
        ):
            stats.log_cache_hit()
            records = _history_rows(fresh)

    if not records:
        stats.log_api_call()
        try:
            records = await fetch(_client.get_history, symbol, fetch_start_date, fetch_end_date, source_interval)
        except PROVIDER_FAILURES as error:
            # Chart indicators may have less warmup in an older cache. Backtests still require their full coverage.
            if not allows_stale(error) or not _covers_history(
                cached_records, fetch_start_date if warmup_days else return_start_date,
                end_day if warmup_days else None, coverage.get(symbol), window.usable_since,
            ):
                raise
            cached_at = min(row.cached_at for row in cached_records)
            if cached_at >= window.fresh_since:
                raise
            records = _history_rows(cached_records)
            metadata = stale_fields(f"history:{symbol}", cached_at, error)
        else:
            if source_interval == "1d":
                for row in records:
                    await upsert_cache(db, StockPrice, {
                        "symbol": symbol,
                        "interval": "1d",
                        "timestamp": datetime.fromisoformat(row["date"]),
                        "open": row.get("open"),
                        "high": row.get("high"),
                        "low": row.get("low"),
                        "close": row["close"],
                        "volume": row.get("volume"),
                    })
                await db.commit()
                if end_day is not None and records:
                    # Re-read after commit so a concurrent shorter fetch cannot replace a fresh watermark.
                    covered_end, covered_at = coverage.get(symbol, (date.min, datetime.min))
                    if end_day >= covered_end or covered_at < window.fresh_since:
                        coverage[symbol] = (end_day, to_db())

    # 1wk/1moは日次データから再集計する
    if requested_interval in {"1wk", "1mo"}:
        records = _aggregate_history(records, requested_interval)

    # 指標の計算
    if records:
        closes = [r["close"] for r in records]
        sma_25 = _calculate_sma(closes, 25)
        sma_50 = _calculate_sma(closes, 50)
        sma_75 = _calculate_sma(closes, 75)
        sma_200 = _calculate_sma(closes, 200)
        rsi_14 = _calculate_rsi(closes, 14)

        for key, values in (
            ("sma_25", sma_25), ("sma_50", sma_50), ("sma_75", sma_75), ("sma_200", sma_200), ("rsi_14", rsi_14),
        ):
            for record, value in zip(records, values, strict=True):
                record[key] = round(value, 2) if value is not None else None

    # 要求されたオリジナル期間でフィルタリング
    if return_start_date:
        records = [r for r in records if r["date"] >= return_start_date]
    if end_date:
        records = [r for r in records if r["date"] <= end_date]

    return {"symbol": symbol, "history": records, **metadata}
