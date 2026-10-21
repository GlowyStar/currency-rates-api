import datetime as dt

import httpx
from clickhouse_connect.driver import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from currency_rates.ingestion.frankfurter import FrankfurterClient
from currency_rates.ingestion.worker import sync_currencies, sync_rates
from currency_rates.storage import postgres

from .seed import CURRENCIES

TODAY = dt.date(2026, 1, 9)
EUR_TO = {"USD": 1.25, "GBP": 0.8}


def _ecb_on_weekdays(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v2/currencies":
        return httpx.Response(
            200, json=[{"iso_code": code, "name": name} for code, name in CURRENCIES.items()]
        )

    start = dt.date.fromisoformat(request.url.params["from"])
    end = dt.date.fromisoformat(request.url.params["to"])
    rows = []
    for offset in range((end - start).days + 1):
        day = start + dt.timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for quote, rate in EUR_TO.items():
            rows.append({"date": day.isoformat(), "base": "EUR", "quote": quote, "rate": rate})
    return httpx.Response(200, json=rows)


def _frankfurter(requests: list[httpx.Request]) -> FrankfurterClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _ecb_on_weekdays(request)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://frankfurter.test")
    return FrankfurterClient(http, backoff_seconds=0)


def _periods(requests: list[httpx.Request]) -> list[tuple[str, str]]:
    return [(request.url.params["from"], request.url.params["to"]) for request in requests]


async def _count_final(client: AsyncClient) -> int:
    result = await client.query("SELECT count() FROM rates FINAL")
    count: int = result.result_rows[0][0]
    return count


async def test_first_sync_backfills_and_the_next_one_reloads_only_the_last_day(
    clickhouse_client: AsyncClient,
) -> None:
    requests: list[httpx.Request] = []
    frankfurter = _frankfurter(requests)

    await sync_rates(frankfurter, clickhouse_client, today=TODAY, backfill_days=8)
    await sync_rates(frankfurter, clickhouse_client, today=TODAY, backfill_days=8)

    assert _periods(requests) == [("2026-01-01", "2026-01-09"), ("2026-01-09", "2026-01-09")]
    weekdays, ordered_pairs = 7, 6
    assert await _count_final(clickhouse_client) == weekdays * ordered_pairs


async def test_long_backfill_is_loaded_in_yearly_windows(
    clickhouse_client: AsyncClient,
) -> None:
    requests: list[httpx.Request] = []

    await sync_rates(_frankfurter(requests), clickhouse_client, today=TODAY, backfill_days=400)

    assert _periods(requests) == [("2024-12-05", "2025-12-04"), ("2025-12-05", "2026-01-09")]


async def test_currency_catalog_is_synced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await sync_currencies(_frankfurter([]), session_factory)

    async with session_factory() as session:
        assert await postgres.get_currency_codes(session) == set(CURRENCIES)
