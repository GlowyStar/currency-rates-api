import asyncio
import datetime as dt
import logging
from collections import defaultdict
from collections.abc import Iterable
from itertools import permutations

import httpx
from clickhouse_connect.driver import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from currency_rates.domain import Rate
from currency_rates.errors import UpstreamError
from currency_rates.ingestion.frankfurter import ECB_PIVOT, FrankfurterClient
from currency_rates.settings import Settings
from currency_rates.storage import clickhouse, postgres

WINDOW_DAYS = 365
# The ECB publishes five or six significant digits; a cross rate has no more precision.
SIGNIFICANT_DIGITS = 6

logger = logging.getLogger(__name__)


def _round_significant(value: float) -> float:
    return float(f"{value:.{SIGNIFICANT_DIGITS}g}")


def derive_cross_rates(eur_rates: Iterable[Rate]) -> list[Rate]:
    """Expands ECB rates against EUR into rates for every ordered pair quoted that day."""
    eur_to_by_date: defaultdict[dt.date, dict[str, float]] = defaultdict(
        lambda: {ECB_PIVOT: 1.0}
    )
    for rate in eur_rates:
        eur_to_by_date[rate.date][rate.quote] = rate.value

    crosses = []
    for date, eur_to in eur_to_by_date.items():
        for (base, base_value), (quote, quote_value) in permutations(eur_to.items(), 2):
            cross_value = _round_significant(quote_value / base_value)
            crosses.append(Rate(base, quote, date, cross_value))
    return crosses


async def sync_currencies(
    frankfurter: FrankfurterClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    names_by_code = await frankfurter.currencies()
    async with session_factory() as session:
        await postgres.upsert_currencies(session, names_by_code)
        await session.commit()
    logger.info("Currency catalog synced: %d currencies", len(names_by_code))


async def sync_rates(
    frankfurter: FrankfurterClient,
    clickhouse_client: AsyncClient,
    *,
    today: dt.date,
    backfill_days: int,
) -> None:
    """Loads rates from the last stored day (or the backfill start) up to today.

    The last stored day is loaded again on purpose: ClickHouse replaces its rows, and a
    day fetched before the ECB published it gets completed.
    """
    last_date = await clickhouse.last_rate_date(clickhouse_client)
    start = last_date or today - dt.timedelta(days=backfill_days)
    while start <= today:
        end = min(start + dt.timedelta(days=WINDOW_DAYS - 1), today)
        rates = derive_cross_rates(await frankfurter.eur_rates(start, end))
        await clickhouse.insert_rates(clickhouse_client, rates)
        logger.info("Rates from %s to %s synced: %d rows", start, end, len(rates))
        start = end + dt.timedelta(days=1)


async def run(settings: Settings) -> None:
    engine = create_async_engine(settings.postgres_dsn)
    session_factory = postgres.create_session_factory(engine)
    clickhouse_client = await clickhouse.connect(settings)

    try:
        async with httpx.AsyncClient(base_url=settings.frankfurter_url, timeout=30) as http:
            frankfurter = FrankfurterClient(http)
            while True:
                try:
                    await sync_currencies(frankfurter, session_factory)
                    await sync_rates(
                        frankfurter,
                        clickhouse_client,
                        today=dt.datetime.now(dt.UTC).date(),
                        backfill_days=settings.backfill_days,
                    )
                except UpstreamError:
                    logger.exception("Sync failed, the next attempt runs on schedule")
                await asyncio.sleep(settings.poll_interval_seconds)
    finally:
        await clickhouse_client.close()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    asyncio.run(run(Settings()))


if __name__ == "__main__":
    main()
