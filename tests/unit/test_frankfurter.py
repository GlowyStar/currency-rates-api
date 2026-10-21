import datetime as dt
from collections.abc import Callable

import httpx
import pytest
from pydantic import ValidationError

from currency_rates.domain import Rate
from currency_rates.errors import UpstreamError
from currency_rates.ingestion.frankfurter import FrankfurterClient

Handler = Callable[[httpx.Request], httpx.Response]

RATES_PAYLOAD = [
    {"date": "2026-10-20", "base": "EUR", "quote": "USD", "rate": 1.1225},
    {"date": "2026-10-21", "base": "EUR", "quote": "USD", "rate": 1.1253},
]


def _client(handler: Handler, attempts: int = 3) -> FrankfurterClient:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://frankfurter.test"
    )
    return FrankfurterClient(http, attempts=attempts, backoff_seconds=0)


def _failing_then_ok(failures: list[httpx.Response | Exception]) -> tuple[Handler, list[str]]:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) <= len(failures):
            failure = failures[len(calls) - 1]
            if isinstance(failure, Exception):
                raise failure
            return failure
        return httpx.Response(200, json=RATES_PAYLOAD)

    return handler, calls


async def test_eur_rates_asks_for_ecb_rates_in_the_period() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=RATES_PAYLOAD)

    rates = await _client(handler).eur_rates(dt.date(2026, 10, 20), dt.date(2026, 10, 21))

    assert rates == [
        Rate("EUR", "USD", dt.date(2026, 10, 20), 1.1225),
        Rate("EUR", "USD", dt.date(2026, 10, 21), 1.1253),
    ]
    [request] = requests
    assert request.url.path == "/v2/rates"
    assert dict(request.url.params) == {
        "providers": "ECB",
        "base": "EUR",
        "from": "2026-10-20",
        "to": "2026-10-21",
    }


async def test_currencies_maps_codes_to_names() -> None:
    payload = [
        {"iso_code": "EUR", "name": "Euro", "symbol": "€"},
        {"iso_code": "USD", "name": "US Dollar", "symbol": "$"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    assert await _client(handler).currencies() == {"EUR": "Euro", "USD": "US Dollar"}


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(503),
        httpx.Response(429),
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("timed out"),
    ],
)
async def test_transient_failures_are_retried(failure: httpx.Response | Exception) -> None:
    handler, calls = _failing_then_ok([failure, failure])

    rates = await _client(handler, attempts=3).eur_rates(
        dt.date(2026, 10, 20), dt.date(2026, 10, 21)
    )

    assert len(rates) == 2
    assert len(calls) == 3


async def test_gives_up_after_the_last_attempt() -> None:
    handler, calls = _failing_then_ok([httpx.Response(502)] * 5)

    with pytest.raises(UpstreamError) as raised:
        await _client(handler, attempts=3).currencies()

    assert raised.value.attempts == 3
    assert len(calls) == 3


async def test_client_errors_are_not_retried() -> None:
    handler, calls = _failing_then_ok([httpx.Response(422, json={"message": "invalid"})])

    with pytest.raises(UpstreamError):
        await _client(handler).eur_rates(dt.date(2026, 10, 20), dt.date(2026, 10, 21))

    assert len(calls) == 1


async def test_non_positive_rate_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"date": "2026-10-21", "base": "EUR", "quote": "USD", "rate": 0}]
        )

    with pytest.raises(ValidationError):
        await _client(handler).eur_rates(dt.date(2026, 10, 21), dt.date(2026, 10, 21))
