"""Process counters and persisted UTC daily request totals."""

import asyncio
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert

from app import database
from app.models.db_models import ApiStatsDaily
from app.timeutil import utcnow

logger = logging.getLogger(__name__)


@dataclass
class Totals:
    hits: int = 0
    errors: int = 0
    total_ms: float = 0.0

    def add(self, hits: int, errors: int, total_ms: float):
        self.hits += hits
        self.errors += errors
        self.total_ms += total_ms


class AppStats:
    def __init__(self):
        self.api_calls = 0
        self.cache_hits = 0
        self.total_requests = 0
        self.total_response_time = 0.0
        self.errors = 0
        self.endpoint_hits = defaultdict(int)
        self.started_at = utcnow()

        self._pending: defaultdict[tuple[date, str], Totals] = defaultdict(Totals)
        self._db_lock = asyncio.Lock()

    def log_api_call(self):
        self.api_calls += 1

    def log_cache_hit(self):
        self.cache_hits += 1

    def log_request(self, endpoint: str, response_time: float, status_code: int):
        errors = int(status_code >= 400)
        self.total_requests += 1
        self.total_response_time += response_time
        self.endpoint_hits[endpoint] += 1
        self.errors += errors
        self._pending[utcnow().date(), endpoint].add(1, errors, response_time * 1000)

    async def flush(self):
        # Let an in-progress transaction finish before propagating cancellation.
        task = asyncio.create_task(self._flush())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _flush(self):
        async with self._db_lock:
            pending, self._pending = self._pending, defaultdict(Totals)
            committed = False
            try:
                async with database.AsyncSessionLocal() as db:
                    if pending:
                        stmt = insert(ApiStatsDaily)
                        stmt = stmt.on_conflict_do_update(
                            index_elements=[ApiStatsDaily.date, ApiStatsDaily.endpoint],
                            set_={name: getattr(ApiStatsDaily, name) + getattr(stmt.excluded, name)
                                  for name in ("hits", "errors", "total_ms")},
                        )
                        await db.execute(stmt, [
                            {"date": day, "endpoint": endpoint, **asdict(totals)}
                            for (day, endpoint), totals in pending.items()
                        ])
                    cutoff = utcnow().date() - timedelta(days=90)
                    await db.execute(delete(ApiStatsDaily).where(ApiStatsDaily.date < cutoff))
                    await db.commit()
                    committed = True
            except Exception:
                logger.exception("Could not flush daily API statistics")
            finally:
                if not committed:
                    for key, totals in pending.items():
                        self._pending[key].add(totals.hits, totals.errors, totals.total_ms)
            return committed

    async def flush_periodically(self, seconds):
        while True:
            await asyncio.sleep(seconds)
            await self.flush()

    async def daily_stats(self):
        today = utcnow().date()
        days = {today - timedelta(days=i): Totals() for i in range(6, -1, -1)}
        async with self._db_lock, database.AsyncSessionLocal() as db:
            result = await db.execute(
                select(ApiStatsDaily.date, func.sum(ApiStatsDaily.hits),
                       func.sum(ApiStatsDaily.errors), func.sum(ApiStatsDaily.total_ms))
                .where(ApiStatsDaily.date.between(next(iter(days)), today))
                .group_by(ApiStatsDaily.date)
            )
            for day, hits, errors, total_ms in result:
                days[day].add(hits, errors, total_ms)
            for (day, _), totals in self._pending.items():
                if day in days:
                    days[day].add(totals.hits, totals.errors, totals.total_ms)

        last_7_days = [{"date": day.isoformat(), **asdict(totals)} for day, totals in days.items()]
        return {"today": last_7_days[-1], "last_7_days": last_7_days}

    def get_stats(self) -> dict:
        data_total = self.api_calls + self.cache_hits
        hit_rate = self.cache_hits / data_total if data_total else None
        avg_response_time = 0.0
        error_rate = 0.0
        if self.total_requests:
            avg_response_time = round(self.total_response_time / self.total_requests * 1000, 2)
            error_rate = round(self.errors / self.total_requests * 100, 1)
        top_endpoints = sorted(
            [{"endpoint": endpoint, "count": count} for endpoint, count in self.endpoint_hits.items()],
            key=lambda item: item["count"],
            reverse=True,
        )[:5]
        return {
            "api_calls": self.api_calls,
            "cache_hits": self.cache_hits,
            "total_requests": data_total,
            "hit_rate_percent": round((hit_rate or 0.0) * 100, 1),
            "cache_hit_rate": hit_rate,
            "server_requests": self.total_requests,
            "server_errors": self.errors,
            "error_rate_percent": error_rate,
            "avg_response_time_ms": avg_response_time,
            "top_endpoints": top_endpoints,
            "uptime_seconds": int((utcnow() - self.started_at).total_seconds()),
        }


stats = AppStats()
