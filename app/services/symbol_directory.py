"""Transactional refresh and in-memory matching for the symbol directory."""

import asyncio
import logging
import re
import unicodedata
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import chain
from time import monotonic

import httpx
from sqlalchemy import delete, insert, select
from sqlalchemy.exc import SQLAlchemyError

from app import database
from app.clients.symbol_lists import (
    JPX_PAGE,
    JPX_XLSX,
    NASDAQ_LISTED,
    OTHER_LISTED,
    SymbolListError,
    discover_jpx_link,
    download,
    parse_jpx,
    parse_nasdaq,
)
from app.clients.yfinance_client import YFinanceClient
from app.config import Settings
from app.exceptions import ExternalAPIError, RateLimitError, SymbolNotFoundError
from app.models.db_models import CompanyProfile, SymbolDirectoryEntry
from app.schemas.symbols import Market, SymbolMatch
from app.singleflight import flight
from app.stats import stats
from app.timeutil import to_db
from app.validation import normalize_symbol

logger = logging.getLogger(__name__)
_yahoo = YFinanceClient()
_CORPORATE = re.compile(r"株式会社|\(株\)|(?<![a-z0-9])(?:corporation|corp|inc|co|ltd|plc)\.?(?![a-z0-9])")
_SEPARATORS = re.compile(r"[\s・·]+")
_JAPANESE = re.compile(
    r"[々〆〇\u3041-\u309f\u30a1-\u30fa\u30fc-\u30ff\u31f0-\u31ff"
    r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U0001aff0-\U0001b16f"
    r"\U00020000-\U0002fa1f\U00030000-\U000323af]"
)
_WORD_START = re.compile(r"(?<!\w)\w")
_KANA = str.maketrans({code: code + 0x60 for code in range(0x3041, 0x3097)} | {0x309D: 0x30FD, 0x309E: 0x30FE})
_ALIASES = {"7203.T": "トヨタ", "6758.T": "ソニー"}
_YAHOO_TTL = 86400
_YAHOO_CACHE_SIZE = 1024
_YAHOO_US_EXCHANGES = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NYQ": "NYSE", "NYS": "NYSE", "ASE": "NYSE American",
    "PCX": "NYSE Arca", "BTS": "Cboe BZX", "IEX": "IEX", "PNK": "OTC", "OQB": "OTC", "OQX": "OTC",
}


def _name_text(value: str) -> str:
    name = unicodedata.normalize("NFKC", value).casefold().translate(_KANA)
    return _CORPORATE.sub("", name)


def normalize_name(value: str) -> str:
    return _SEPARATORS.sub("", _name_text(value)).strip(",.")


def _bigrams(value: str) -> frozenset[str]:
    return frozenset(value[index:index + 2] for index in range(len(value) - 1))


@dataclass(frozen=True, slots=True)
class _IndexedSymbol:
    match: SymbolMatch
    ticker: str
    code: str
    name: str
    alias: str
    word_starts: frozenset[int] | None
    bigrams: frozenset[str]

    @classmethod
    def build(cls, match: SymbolMatch, key: str | None = None):
        text = _name_text(match.name)
        if _JAPANESE.search(text):
            name = _SEPARATORS.sub("", text).strip(",.") if key is None else key
            word_starts = None
        else:
            # Rebuild Latin names from the display name to recover boundaries lost in stored keys.
            name = _SEPARATORS.sub(" ", text).strip(" ,.")
            word_starts = frozenset(word.start() for word in _WORD_START.finditer(name))
        ticker = match.symbol.casefold()
        code = ticker.removesuffix(".t") if match.market == "JP" else ticker
        return cls(match, ticker, code, name, _ALIASES.get(match.symbol, ""), word_starts, _bigrams(name))

    def name_score(self, name: str) -> tuple[int, float]:
        if not name:
            return (0, 0)
        if name in (self.name, self.alias):
            return (3, 1)
        if self.name.startswith(name):
            return (3, 0)
        if self.word_starts is None:
            return (2, 0) if name in self.name else (0, 0)
        position = self.name.find(name)
        while position >= 0:
            if position in self.word_starts:
                return (2, 0)
            position = self.name.find(name, position + 1)
        return (0, 0)

    def fuzzy_score(self, name: str, bigrams: frozenset[str]) -> float:
        # An exact substring that missed the strong tiers also missed a word boundary.
        if len(bigrams) < 2 or name in self.name:
            return 0
        common = len(bigrams & self.bigrams)
        if common < 2:
            return 0
        score = 2 * common / (len(bigrams) + len(self.bigrams))
        return score if score >= 0.57 else 0


class SymbolIndex:
    def __init__(self, rows: Iterable[tuple[SymbolMatch, str | None]] = ()):
        self.entries = {match.symbol: _IndexedSymbol.build(match, key) for match, key in rows}
        self.profiles: dict[str, _IndexedSymbol] = {}

    def remember_profile(self, symbol: str, name: str | None, sector: str | None) -> None:
        if not name or not symbol.endswith(".T"):
            return
        self.profiles[symbol] = _IndexedSymbol.build(SymbolMatch(symbol, name, "JP", None, sector, False, "profile"))

    def search(self, query: str, limit: int = 10, extra: Iterable[SymbolMatch] = ()) -> list[SymbolMatch]:
        return self._search(query, limit, extra)[0]

    def _search(self, query: str, limit: int, extra: Iterable[SymbolMatch] = ()) -> tuple[list[SymbolMatch], bool]:
        ticker = unicodedata.normalize("NFKC", query).strip().casefold()
        if not ticker:
            return [], True
        text = _name_text(ticker)
        compact_name = _SEPARATORS.sub("", text).strip(",.")
        spaced_name = _SEPARATORS.sub(" ", text).strip(" ,.")
        ranked: dict[str, tuple[tuple[int, float, bool, bool, str], SymbolMatch]] = {}
        skip_yahoo = False

        def rank(entry: _IndexedSymbol, tier: int, score: float) -> None:
            match = entry.match
            if match.source == "profile" and (existing := self.entries.get(match.symbol)):
                match = replace(
                    match, segment=existing.match.segment, sector=existing.match.sector or match.sector,
                    is_etf=existing.match.is_etf,
                )
            key = (-tier, -score, match.is_etf, match.segment != "プライム", match.symbol)
            previous = ranked.get(match.symbol)
            if previous is None or key < previous[0]:
                ranked[match.symbol] = key, match

        def consider_strong(entry: _IndexedSymbol) -> bool:
            if ticker in (entry.ticker, entry.code):
                rank(entry, 5, 0)
                return True
            name = compact_name if entry.word_starts is None else spaced_name
            tier, score = entry.name_score(name)
            if entry.ticker.startswith(ticker):
                rank(entry, 4, 0)
            elif tier:
                rank(entry, tier, score)
            return tier > 0

        for entry in chain(self.entries.values(), self.profiles.values()):
            skip_yahoo |= consider_strong(entry)
        # Ticker prefixes alone suppress Yahoo only when there are at least three matches.
        skip_yahoo = skip_yahoo or len(ranked) >= 3
        for match in extra:
            if match.symbol not in ranked:
                consider_strong(_IndexedSymbol.build(match))
        if not ranked and len(compact_name) >= 3:
            compact_bigrams, spaced_bigrams = _bigrams(compact_name), _bigrams(spaced_name)
            for entry in chain(self.entries.values(), self.profiles.values()):
                name, bigrams = (compact_name, compact_bigrams) if entry.word_starts is None else (spaced_name, spaced_bigrams)
                if score := entry.fuzzy_score(name, bigrams):
                    rank(entry, 1, score)
        return [match for _, match in sorted(ranked.values(), key=lambda hit: hit[0])[:limit]], skip_yahoo


def _entry_match(entry: SymbolDirectoryEntry) -> SymbolMatch:
    return SymbolMatch(entry.symbol, entry.name, entry.market, entry.segment, entry.sector, entry.is_etf, entry.source)


def _yahoo_matches(quotes: list[dict]) -> list[SymbolMatch]:
    matches = []
    for quote in quotes:
        if not isinstance(quote, dict) or quote.get("quoteType") not in ("EQUITY", "ETF"):
            continue
        symbol = quote.get("symbol")
        if not isinstance(symbol, str):
            continue
        try:
            symbol = normalize_symbol(symbol)
        except ValueError:
            continue
        exchange = quote.get("exchange")
        segment = _YAHOO_US_EXCHANGES.get(exchange) if isinstance(exchange, str) else None
        if symbol.endswith(".T"):
            market, segment = "JP", None
        elif re.fullmatch(r"[A-Z]+(?:-[A-Z]+)?", symbol) and (segment or exchange is None):
            market = "US"
        else:
            continue
        name = next((value.strip() for value in (quote.get("shortname"), quote.get("longname"))
                     if isinstance(value, str) and value.strip()), symbol)
        sector = quote.get("sector")
        matches.append(SymbolMatch(
            symbol, name, market, segment, sector if isinstance(sector, str) else None,
            quote["quoteType"] == "ETF", "yahoo",
        ))
    return matches


class SymbolDirectory:
    def __init__(self):
        self.index = SymbolIndex()
        self._yahoo_cache: OrderedDict[str, tuple[float, list[SymbolMatch]]] = OrderedDict()

    async def load(self) -> dict[Market, datetime]:
        current = self.index
        previous = current.profiles.copy()
        async with database.AsyncSessionLocal() as db:
            entries = (await db.scalars(select(SymbolDirectoryEntry))).all()
            profiles = (await db.execute(select(CompanyProfile.symbol, CompanyProfile.name, CompanyProfile.sector))).all()

        def build() -> tuple[SymbolIndex, dict[Market, datetime]]:
            index = SymbolIndex((_entry_match(entry), entry.search_key) for entry in entries)
            dates: dict[Market, datetime] = {}
            for entry in entries:
                dates[entry.market] = min(dates.get(entry.market, entry.updated_at), entry.updated_at)
            for symbol, name, sector in profiles:
                index.remember_profile(symbol, name, sector)
            return index, dates

        index, dates = await asyncio.to_thread(build)
        # Retain profile writes that completed while the database snapshot was being indexed.
        index.profiles.update({key: value for key, value in current.profiles.items() if value is not previous.get(key)})
        self.index = index
        return dates

    async def search(self, query: str, limit: int) -> list[SymbolMatch]:
        key = unicodedata.normalize("NFKC", query).casefold()
        local, skip_yahoo = self.index._search(query, limit)
        if skip_yahoo or _JAPANESE.search(key):
            return local

        async def lookup() -> list[SymbolMatch]:
            if cached := self._yahoo_cache.get(key):
                if cached[0] > monotonic():
                    self._yahoo_cache.move_to_end(key)
                    return cached[1]
            stats.log_api_call()
            try:
                quotes = await asyncio.to_thread(_yahoo.search_symbols, query)
                matches = _yahoo_matches(quotes)
            except (ExternalAPIError, RateLimitError, SymbolNotFoundError) as exc:
                logger.warning("Yahoo 銘柄検索に失敗しました（%s）。", type(exc).__name__)
                return []
            self._yahoo_cache[key] = (monotonic() + _YAHOO_TTL, matches)
            self._yahoo_cache.move_to_end(key)
            if len(self._yahoo_cache) > _YAHOO_CACHE_SIZE:
                self._yahoo_cache.popitem(last=False)
            return matches

        extra = await flight.run((self, key), lookup)
        return self.index.search(query, limit, extra) if extra else local


directory = SymbolDirectory()


async def _download_market(client: httpx.AsyncClient, market: Market) -> list[SymbolMatch]:
    if market == "JP":
        try:
            page = await download(client, JPX_PAGE)
            link = discover_jpx_link(page.decode("utf-8", errors="replace"))
        except (httpx.HTTPError, SymbolListError):
            link = None
        # The historical .xls link is discoverable, but only OOXML can be parsed.
        url = link if link and link.lower().endswith(".xlsx") else JPX_XLSX
        try:
            data = await download(client, url)
        except httpx.HTTPError:
            if url == JPX_XLSX:
                raise
            data = await download(client, JPX_XLSX)
        return await asyncio.to_thread(parse_jpx, data)
    nasdaq = await download(client, NASDAQ_LISTED)
    other = await download(client, OTHER_LISTED)
    first = await asyncio.to_thread(parse_nasdaq, nasdaq)
    second = await asyncio.to_thread(parse_nasdaq, other, other=True)
    return list({match.symbol: match for match in [*second, *first]}.values())


async def refresh_symbol_directory(
    markets: Iterable[Market] = ("JP", "US"), *, transport: httpx.AsyncBaseTransport | None = None,
) -> dict[Market, int]:
    """Replace each successfully parsed market in its own transaction."""
    counts: dict[Market, int] = {}
    async with httpx.AsyncClient(
        timeout=15, headers={"User-Agent": "tera-quant-lab/1.0", "Accept-Encoding": "identity"}, transport=transport,
    ) as client:
        for market in markets:
            try:
                async with asyncio.timeout(60):
                    matches = await _download_market(client, market)
                updated_at = to_db()
                rows = [
                    dict(symbol=match.symbol, name=match.name, search_key=normalize_name(match.name), market=market,
                         segment=match.segment, sector=match.sector, is_etf=match.is_etf, source=match.source,
                         updated_at=updated_at)
                    for match in matches
                ]
                async with database.AsyncSessionLocal.begin() as db:
                    await db.execute(delete(SymbolDirectoryEntry).where(SymbolDirectoryEntry.market == market))
                    await db.execute(insert(SymbolDirectoryEntry), rows)
            except (httpx.HTTPError, TimeoutError, SymbolListError, SQLAlchemyError) as exc:
                logger.warning("銘柄ディレクトリの %s を更新できませんでした。既存データを使用します（%s）。", market, exc)
                continue
            counts[market] = len(matches)
        if counts:
            await directory.load()
    return counts


async def initialize_symbol_directory(settings: Settings) -> None:
    try:
        dates = await directory.load()
        if settings.symbol_directory_enabled:
            cutoff = to_db() - timedelta(days=settings.symbol_directory_max_age_days)
            markets: tuple[Market, ...] = ("JP", "US")
            stale: list[Market] = [market for market in markets if market not in dates or dates[market] < cutoff]
            if stale:
                await refresh_symbol_directory(stale)
    except SQLAlchemyError:
        logger.warning("銘柄ディレクトリを読み込めませんでした。", exc_info=True)
