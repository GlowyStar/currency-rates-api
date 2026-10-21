import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from clickhouse_connect.driver import AsyncClient
from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from currency_rates.errors import (
    DomainError,
    InvalidApiKeyError,
    InvalidRequestError,
    RatesNotFoundError,
    UnknownCurrencyError,
)
from currency_rates.services import api_keys, rates
from currency_rates.settings import Settings
from currency_rates.storage import clickhouse, postgres

STATUS_BY_ERROR: dict[type[Exception], int] = {
    UnknownCurrencyError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidRequestError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    RatesNotFoundError: status.HTTP_404_NOT_FOUND,
    InvalidApiKeyError: status.HTTP_401_UNAUTHORIZED,
}

API_KEY_HEADER = APIKeyHeader(name="X-API-Key")


@dataclass(frozen=True, slots=True)
class Resources:
    session_factory: async_sessionmaker[AsyncSession]
    clickhouse: AsyncClient


class CurrencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str


class LatestRatesOut(BaseModel):
    base: str
    date: dt.date
    rates: dict[str, float]


class HistoryPointOut(BaseModel):
    date: dt.date
    rate: float


class HistoryOut(BaseModel):
    base: str
    symbol: str
    bucket_days: int
    points: list[HistoryPointOut]


class HealthOut(BaseModel):
    postgres: bool
    clickhouse: bool


def get_resources(request: Request) -> Resources:
    resources: Resources = request.app.state.resources
    return resources


ResourcesDep = Annotated[Resources, Depends(get_resources)]


async def get_session(resources: ResourcesDep) -> AsyncIterator[AsyncSession]:
    async with resources.session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def authenticate(
    api_key: Annotated[str, Depends(API_KEY_HEADER)], session: SessionDep
) -> None:
    await api_keys.authenticate(session, api_key)


router = APIRouter()
rates_router = APIRouter(prefix="/v1/rates", dependencies=[Depends(authenticate)])


@router.get("/health", response_model=HealthOut)
async def health(resources: ResourcesDep, response: Response) -> HealthOut:
    result = HealthOut(
        postgres=await postgres.ping(resources.session_factory),
        clickhouse=await resources.clickhouse.ping(),
    )
    if not (result.postgres and result.clickhouse):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result


@router.get("/v1/currencies", response_model=list[CurrencyOut])
async def list_currencies(session: SessionDep) -> list[CurrencyOut]:
    currencies = await rates.list_currencies(session)
    return [CurrencyOut.model_validate(currency) for currency in currencies]


@rates_router.get("/latest", response_model=LatestRatesOut)
async def latest_rates(
    session: SessionDep,
    resources: ResourcesDep,
    base: Annotated[str, Query(examples=["USD"])],
    symbols: Annotated[str, Query(description="Comma separated codes, all if empty")] = "",
) -> LatestRatesOut:
    latest = await rates.get_latest(session, resources.clickhouse, base, symbols.split(","))
    return LatestRatesOut(base=latest.base, date=latest.date, rates=latest.rates)


@rates_router.get("/history", response_model=HistoryOut)
async def rate_history(
    session: SessionDep,
    resources: ResourcesDep,
    base: Annotated[str, Query(examples=["USD"])],
    symbol: Annotated[str, Query(examples=["EUR"])],
    start: dt.date,
    end: dt.date,
    max_points: Annotated[
        int, Query(description=f"At most {rates.MAX_POINTS_LIMIT}")
    ] = rates.DEFAULT_MAX_POINTS,
) -> HistoryOut:
    history = await rates.get_history(
        session,
        resources.clickhouse,
        base=base,
        quote=symbol,
        start=start,
        end=end,
        max_points=max_points,
    )
    points = [HistoryPointOut(date=point.date, rate=point.rate) for point in history.points]
    return HistoryOut(
        base=history.base, symbol=history.quote, bucket_days=history.bucket_days, points=points
    )


async def handle_domain_error(request: Request, error: Exception) -> JSONResponse:
    status_code = STATUS_BY_ERROR[type(error)]
    headers = {"WWW-Authenticate": "APIKey"} if isinstance(error, InvalidApiKeyError) else None
    return JSONResponse({"detail": str(error)}, status_code=status_code, headers=headers)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(config.postgres_dsn)
        clickhouse_client = await clickhouse.connect(config)
        app.state.resources = Resources(
            postgres.create_session_factory(engine), clickhouse_client
        )
        try:
            yield
        finally:
            await clickhouse_client.close()
            await engine.dispose()

    app = FastAPI(title="Currency Rates API", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    app.include_router(rates_router)
    app.add_exception_handler(DomainError, handle_domain_error)
    return app
