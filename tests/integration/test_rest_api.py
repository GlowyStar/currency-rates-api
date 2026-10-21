from collections.abc import AsyncIterator

import httpx
import pytest

from currency_rates.api.rest import create_app
from currency_rates.settings import Settings

pytestmark = pytest.mark.usefixtures("seeded")


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as http,
    ):
        yield http


async def test_health_reports_both_databases(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"postgres": True, "clickhouse": True}


async def test_currencies_are_public(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/currencies")

    assert response.status_code == 200
    assert [currency["code"] for currency in response.json()] == ["EUR", "GBP", "JPY", "USD"]


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "not-a-key"}])
async def test_rates_need_a_valid_api_key(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get("/v1/rates/latest", params={"base": "USD"}, headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "APIKey"


async def test_latest_rates(client: httpx.AsyncClient, api_key: str) -> None:
    response = await client.get(
        "/v1/rates/latest",
        params={"base": "usd", "symbols": "EUR,gbp"},
        headers={"X-API-Key": api_key},
    )

    assert response.status_code == 200
    assert response.json() == {
        "base": "USD",
        "date": "2026-01-10",
        "rates": {"EUR": 0.9, "GBP": 0.75},
    }


async def test_latest_rates_without_symbols_return_every_stored_quote(
    client: httpx.AsyncClient, api_key: str
) -> None:
    response = await client.get(
        "/v1/rates/latest", params={"base": "USD"}, headers={"X-API-Key": api_key}
    )

    assert response.json()["rates"] == {"EUR": 0.9, "GBP": 0.75}


async def test_unknown_currency_is_rejected(client: httpx.AsyncClient, api_key: str) -> None:
    response = await client.get(
        "/v1/rates/latest",
        params={"base": "USD", "symbols": "EUR,XXX"},
        headers={"X-API-Key": api_key},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Unknown currency code(s): XXX"}


async def test_base_without_rates_is_not_found(
    client: httpx.AsyncClient, api_key: str
) -> None:
    response = await client.get(
        "/v1/rates/latest", params={"base": "JPY"}, headers={"X-API-Key": api_key}
    )

    assert response.status_code == 404


async def test_history_is_downsampled(client: httpx.AsyncClient, api_key: str) -> None:
    params = {
        "base": "USD",
        "symbol": "EUR",
        "start": "2026-01-01",
        "end": "2026-01-10",
        "max_points": "5",
    }

    response = await client.get(
        "/v1/rates/history", params=params, headers={"X-API-Key": api_key}
    )

    body = response.json()
    assert response.status_code == 200
    assert body["bucket_days"] == 2
    assert [point["date"] for point in body["points"]] == [
        "2026-01-01",
        "2026-01-03",
        "2026-01-05",
        "2026-01-07",
        "2026-01-09",
    ]
    assert [round(point["rate"], 6) for point in body["points"]] == [
        0.815,
        0.835,
        0.855,
        0.875,
        0.895,
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"start": "2026-01-10", "end": "2026-01-01"},
        {"max_points": "0"},
        {"max_points": "1001"},
        {"symbol": "USD"},
    ],
)
async def test_invalid_history_requests_are_rejected(
    client: httpx.AsyncClient, api_key: str, overrides: dict[str, str]
) -> None:
    params = {"base": "USD", "symbol": "EUR", "start": "2026-01-01", "end": "2026-01-10"}

    response = await client.get(
        "/v1/rates/history", params=params | overrides, headers={"X-API-Key": api_key}
    )

    assert response.status_code == 422
