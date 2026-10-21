import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass

import clickhouse_connect
from clickhouse_connect.driver import AsyncClient

from currency_rates.domain import HistoryPoint, LatestRates, Rate
from currency_rates.settings import Settings

# ReplacingMergeTree keeps the row with the latest ingested_at per (base, quote, date),
# so loading the same days again never duplicates them.
SCHEMA = """
CREATE TABLE IF NOT EXISTS rates
(
    base LowCardinality(String),
    quote LowCardinality(String),
    date Date,
    rate Float64,
    ingested_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYear(date)
ORDER BY (base, quote, date)
"""

RATE_COLUMNS = ("base", "quote", "date", "rate")

LATEST_SQL = """
SELECT quote, rate, date
FROM rates FINAL
WHERE base = {base:String}
    AND has({quotes:Array(String)}, quote)
    AND date = (SELECT max(date) FROM rates WHERE base = {base:String})
ORDER BY quote
"""


# Days are grouped into equal buckets counted from the start of the period and every
# bucket is the average of its rates, so a chart never gets more than max_points points.
HISTORY_SQL = """
SELECT
    addDays(
        {start:Date},
        {bucket_days:UInt32}
            * intDiv(dateDiff('day', {start:Date}, date), {bucket_days:UInt32})
    ) AS bucket,
    avg(rate) AS average_rate
FROM rates FINAL
WHERE base = {base:String}
    AND quote = {quote:String}
    AND date BETWEEN {start:Date} AND {end:Date}
GROUP BY bucket
ORDER BY bucket
"""


@dataclass(frozen=True, slots=True)
class HistoryQuery:
    sql: str
    parameters: dict[str, object]
    bucket_days: int


def build_history_query(
    base: str, quote: str, *, start: dt.date, end: dt.date, max_points: int
) -> HistoryQuery:
    period_days = (end - start).days + 1
    bucket_days = math.ceil(period_days / max_points)
    parameters: dict[str, object] = {
        "base": base,
        "quote": quote,
        "start": start,
        "end": end,
        "bucket_days": bucket_days,
    }
    return HistoryQuery(HISTORY_SQL, parameters, bucket_days)


async def connect(settings: Settings) -> AsyncClient:
    return await clickhouse_connect.get_async_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
    )


async def create_schema(client: AsyncClient) -> None:
    await client.command(SCHEMA)


async def insert_rates(client: AsyncClient, rates: Sequence[Rate]) -> None:
    if not rates:
        return
    rows = [(rate.base, rate.quote, rate.date, rate.value) for rate in rates]
    await client.insert("rates", rows, column_names=RATE_COLUMNS)


async def last_rate_date(client: AsyncClient) -> dt.date | None:
    result = await client.query("SELECT maxOrNull(date) FROM rates")
    last_date: dt.date | None = result.result_rows[0][0]
    return last_date


async def latest_rates(
    client: AsyncClient, base: str, quotes: Sequence[str]
) -> LatestRates | None:
    result = await client.query(LATEST_SQL, parameters={"base": base, "quotes": list(quotes)})
    rows = result.result_rows
    if not rows:
        return None
    return LatestRates(
        base=base, date=rows[0][2], rates={quote: rate for quote, rate, _ in rows}
    )


async def history(client: AsyncClient, query: HistoryQuery) -> list[HistoryPoint]:
    result = await client.query(query.sql, parameters=query.parameters)
    return [HistoryPoint(bucket, average_rate) for bucket, average_rate in result.result_rows]
