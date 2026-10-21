import datetime as dt
from collections.abc import Iterable, Sequence

from clickhouse_connect.driver import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from currency_rates.domain import History, LatestRates
from currency_rates.errors import (
    InvalidRequestError,
    RatesNotFoundError,
    UnknownCurrencyError,
)
from currency_rates.storage import clickhouse, postgres
from currency_rates.storage.postgres import Currency

DEFAULT_MAX_POINTS = 200
DEFAULT_PERIOD = dt.timedelta(days=365)
MAX_POINTS_LIMIT = 1000


def _normalize(code: str) -> str:
    return code.strip().upper()


def _ensure_known(known: set[str], codes: Iterable[str]) -> None:
    unknown = [code for code in codes if code not in known]
    if unknown:
        raise UnknownCurrencyError(unknown)


async def list_currencies(session: AsyncSession) -> Sequence[Currency]:
    return await postgres.list_currencies(session)


async def get_latest(
    session: AsyncSession, clickhouse_client: AsyncClient, base: str, symbols: Iterable[str]
) -> LatestRates:
    """Returns the rates of the last published day; no symbols means every known currency."""
    base = _normalize(base)
    quotes = [_normalize(symbol) for symbol in symbols if symbol.strip()]
    known = await postgres.get_currency_codes(session)
    _ensure_known(known, [base, *quotes])

    latest = await clickhouse.latest_rates(
        clickhouse_client, base, quotes or sorted(known - {base})
    )
    if latest is None:
        raise RatesNotFoundError(base)
    return latest


async def get_history(
    session: AsyncSession,
    clickhouse_client: AsyncClient,
    *,
    base: str,
    quote: str,
    start: dt.date | None,
    end: dt.date | None,
    max_points: int,
) -> History:
    """Returns the rates of a pair averaged into at most max_points equal day buckets.

    The period ends today (UTC) unless end is given and starts DEFAULT_PERIOD before its
    end unless start is given.
    """
    base, quote = _normalize(base), _normalize(quote)
    end = end if end is not None else dt.datetime.now(dt.UTC).date()
    start = start if start is not None else end - DEFAULT_PERIOD
    if start > end:
        raise InvalidRequestError("start must not be after end")
    if not 1 <= max_points <= MAX_POINTS_LIMIT:
        raise InvalidRequestError(f"max_points must be between 1 and {MAX_POINTS_LIMIT}")
    if base == quote:
        raise InvalidRequestError("base and quote must differ")
    _ensure_known(await postgres.get_currency_codes(session), [base, quote])

    query = clickhouse.build_history_query(
        base, quote, start=start, end=end, max_points=max_points
    )
    points = await clickhouse.history(clickhouse_client, query)
    return History(base, quote, query.bucket_days, points)
