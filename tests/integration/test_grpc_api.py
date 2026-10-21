from collections.abc import AsyncIterator
from typing import Any

import grpc
import pytest
from clickhouse_connect.driver import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from currency_rates.api.grpc_server import RatesServicer
from currency_rates.proto import rates_pb2, rates_pb2_grpc

pytestmark = pytest.mark.usefixtures("seeded")


@pytest.fixture
async def stub(
    session_factory: async_sessionmaker[AsyncSession], clickhouse_client: AsyncClient
) -> AsyncIterator[Any]:
    server = grpc.aio.server()
    rates_pb2_grpc.add_RatesServiceServicer_to_server(
        RatesServicer(session_factory, clickhouse_client), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield rates_pb2_grpc.RatesServiceStub(channel)
    await server.stop(grace=None)


async def test_latest(stub: Any, api_key: str) -> None:
    response = await stub.Latest(
        rates_pb2.LatestRequest(base="USD", symbols=["EUR"]),
        metadata=(("x-api-key", api_key),),
    )

    assert response.base == "USD"
    assert response.date == "2026-01-10"
    assert dict(response.rates) == {"EUR": 0.9}


async def test_history_streams_downsampled_points(stub: Any, api_key: str) -> None:
    request = rates_pb2.HistoryRequest(
        base="USD", symbol="EUR", start="2026-01-01", end="2026-01-10", max_points=2
    )

    points = [
        (point.date, round(point.rate, 6))
        async for point in stub.History(request, metadata=(("x-api-key", api_key),))
    ]

    assert points == [("2026-01-01", 0.83), ("2026-01-06", 0.88)]


async def test_missing_api_key_is_unauthenticated(stub: Any) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as raised:
        await stub.Latest(rates_pb2.LatestRequest(base="USD"))

    assert raised.value.code() == grpc.StatusCode.UNAUTHENTICATED


async def test_base_without_rates_is_not_found(stub: Any, api_key: str) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as raised:
        await stub.Latest(
            rates_pb2.LatestRequest(base="JPY"), metadata=(("x-api-key", api_key),)
        )

    assert raised.value.code() == grpc.StatusCode.NOT_FOUND


@pytest.mark.parametrize(
    "overrides",
    [{"start": "yesterday"}, {"symbol": "XXX"}, {"max_points": 5000}],
)
async def test_invalid_history_requests_are_rejected(
    stub: Any, api_key: str, overrides: dict[str, Any]
) -> None:
    fields = {"base": "USD", "symbol": "EUR", "start": "2026-01-01", "end": "2026-01-10"}
    request = rates_pb2.HistoryRequest(**(fields | overrides))

    with pytest.raises(grpc.aio.AioRpcError) as raised:
        [point async for point in stub.History(request, metadata=(("x-api-key", api_key),))]

    assert raised.value.code() == grpc.StatusCode.INVALID_ARGUMENT
