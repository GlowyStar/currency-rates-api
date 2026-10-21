import asyncio
import datetime as dt
import logging
from collections.abc import AsyncIterator
from typing import Any

import grpc
from clickhouse_connect.driver import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from currency_rates.errors import (
    DomainError,
    InvalidApiKeyError,
    InvalidRequestError,
    RatesNotFoundError,
    UnknownCurrencyError,
)
from currency_rates.proto import rates_pb2, rates_pb2_grpc
from currency_rates.services import api_keys, rates
from currency_rates.settings import Settings
from currency_rates.storage import clickhouse, postgres

API_KEY_METADATA = "x-api-key"

STATUS_BY_ERROR: dict[type[Exception], grpc.StatusCode] = {
    UnknownCurrencyError: grpc.StatusCode.INVALID_ARGUMENT,
    InvalidRequestError: grpc.StatusCode.INVALID_ARGUMENT,
    RatesNotFoundError: grpc.StatusCode.NOT_FOUND,
    InvalidApiKeyError: grpc.StatusCode.UNAUTHENTICATED,
}

Context = grpc.aio.ServicerContext[Any, Any]

logger = logging.getLogger(__name__)


def _status_of(error: DomainError) -> grpc.StatusCode:
    return STATUS_BY_ERROR.get(type(error), grpc.StatusCode.INVALID_ARGUMENT)


def _api_key(context: Context) -> str:
    metadata = dict(context.invocation_metadata() or ())
    api_key = metadata.get(API_KEY_METADATA, "")
    return api_key if isinstance(api_key, str) else ""


def _parse_optional_date(value: str, field: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise InvalidRequestError(f"{field} must be a YYYY-MM-DD date") from error


class RatesServicer(rates_pb2_grpc.RatesServiceServicer):
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], clickhouse_client: AsyncClient
    ) -> None:
        self._session_factory = session_factory
        self._clickhouse = clickhouse_client

    async def Latest(
        self, request: rates_pb2.LatestRequest, context: Context
    ) -> rates_pb2.LatestResponse:
        async with self._session_factory() as session:
            try:
                await api_keys.authenticate(session, _api_key(context))
                latest = await rates.get_latest(
                    session, self._clickhouse, request.base, request.symbols
                )
            except DomainError as error:
                await context.abort(_status_of(error), str(error))

        return rates_pb2.LatestResponse(
            base=latest.base, date=latest.date.isoformat(), rates=latest.rates
        )

    async def History(
        self, request: rates_pb2.HistoryRequest, context: Context
    ) -> AsyncIterator[rates_pb2.HistoryPoint]:
        async with self._session_factory() as session:
            try:
                await api_keys.authenticate(session, _api_key(context))
                history = await rates.get_history(
                    session,
                    self._clickhouse,
                    base=request.base,
                    quote=request.symbol,
                    start=_parse_optional_date(request.start, "start"),
                    end=_parse_optional_date(request.end, "end"),
                    max_points=request.max_points or rates.DEFAULT_MAX_POINTS,
                )
            except DomainError as error:
                await context.abort(_status_of(error), str(error))

        for point in history.points:
            yield rates_pb2.HistoryPoint(date=point.date.isoformat(), rate=point.rate)


async def serve(settings: Settings) -> None:
    engine = create_async_engine(settings.postgres_dsn)
    clickhouse_client = await clickhouse.connect(settings)
    servicer = RatesServicer(postgres.create_session_factory(engine), clickhouse_client)

    server = grpc.aio.server()
    rates_pb2_grpc.add_RatesServiceServicer_to_server(servicer, server)
    server.add_insecure_port(f"[::]:{settings.grpc_port}")
    await server.start()
    logger.info("gRPC server listens on port %d", settings.grpc_port)
    try:
        await server.wait_for_termination()
    finally:
        await server.stop(grace=5)
        await clickhouse_client.close()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    asyncio.run(serve(Settings()))


if __name__ == "__main__":
    main()
