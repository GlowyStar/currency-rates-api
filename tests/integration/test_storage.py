import datetime as dt

import pytest
from clickhouse_connect.driver import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from currency_rates.domain import HistoryPoint, Rate
from currency_rates.storage import clickhouse, postgres

from .seed import FIRST_DAY, LAST_DAY


async def _count_final(client: AsyncClient) -> int:
    result = await client.query("SELECT count() FROM rates FINAL")
    count: int = result.result_rows[0][0]
    return count


async def test_inserting_the_same_days_again_keeps_one_row_per_pair_and_day(
    clickhouse_client: AsyncClient,
) -> None:
    day = dt.date(2026, 1, 5)
    await clickhouse.insert_rates(clickhouse_client, [Rate("USD", "EUR", day, 0.8)])
    await clickhouse.insert_rates(clickhouse_client, [Rate("USD", "EUR", day, 0.8)])

    assert await _count_final(clickhouse_client) == 1


@pytest.mark.usefixtures("seeded")
async def test_last_rate_date(clickhouse_client: AsyncClient) -> None:
    assert await clickhouse.last_rate_date(clickhouse_client) == LAST_DAY


async def test_last_rate_date_of_empty_table_is_none(clickhouse_client: AsyncClient) -> None:
    assert await clickhouse.last_rate_date(clickhouse_client) is None


@pytest.mark.usefixtures("seeded")
async def test_latest_rates_come_from_the_last_day(clickhouse_client: AsyncClient) -> None:
    latest = await clickhouse.latest_rates(clickhouse_client, "USD", ["EUR", "GBP"])

    assert latest is not None
    assert latest.date == LAST_DAY
    assert latest.rates == {"EUR": 0.9, "GBP": 0.75}


@pytest.mark.usefixtures("seeded")
async def test_history_averages_each_bucket(clickhouse_client: AsyncClient) -> None:
    query = clickhouse.build_history_query(
        "USD", "EUR", start=FIRST_DAY, end=LAST_DAY, max_points=3
    )

    points = await clickhouse.history(clickhouse_client, query)

    assert query.bucket_days == 4
    assert [point.date for point in points] == [
        FIRST_DAY,
        FIRST_DAY + dt.timedelta(days=4),
        FIRST_DAY + dt.timedelta(days=8),
    ]
    assert [round(point.rate, 6) for point in points] == [0.825, 0.865, 0.895]


@pytest.mark.usefixtures("seeded")
async def test_history_with_room_for_every_day_returns_raw_rates(
    clickhouse_client: AsyncClient,
) -> None:
    query = clickhouse.build_history_query(
        "USD", "EUR", start=LAST_DAY, end=LAST_DAY + dt.timedelta(days=30), max_points=200
    )

    assert await clickhouse.history(clickhouse_client, query) == [HistoryPoint(LAST_DAY, 0.9)]


async def test_upsert_currencies_updates_names(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await postgres.upsert_currencies(session, {"EUR": "Euro", "USD": "Dollar"})
        await postgres.upsert_currencies(session, {"USD": "US Dollar"})
        await session.commit()

        currencies = await postgres.list_currencies(session)

    assert [(currency.code, currency.name) for currency in currencies] == [
        ("EUR", "Euro"),
        ("USD", "US Dollar"),
    ]
