from collections.abc import Iterable, Sequence

from clickhouse_connect.driver import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from currency_rates.domain import LatestRates
from currency_rates.errors import RatesNotFoundError, UnknownCurrencyError
from currency_rates.storage import clickhouse, postgres
from currency_rates.storage.postgres import Currency


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
