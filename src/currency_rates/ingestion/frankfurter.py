import asyncio
import datetime as dt
import logging
from collections.abc import Mapping

import httpx
from pydantic import BaseModel, PositiveFloat, TypeAdapter, ValidationError

from currency_rates.domain import Rate
from currency_rates.errors import UpstreamError

ECB_PIVOT = "EUR"

logger = logging.getLogger(__name__)


class _RateRow(BaseModel):
    date: dt.date
    base: str
    quote: str
    rate: PositiveFloat


class _CurrencyRow(BaseModel):
    iso_code: str
    name: str


_RATE_ROWS = TypeAdapter(list[_RateRow])
_CURRENCY_ROWS = TypeAdapter(list[_CurrencyRow])


def _validate[T](rows: TypeAdapter[T], content: bytes, path: str) -> T:
    try:
        return rows.validate_json(content)
    except ValidationError as error:
        raise UpstreamError(path, "unexpected response") from error


def _is_retryable(error: httpx.HTTPError) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status == httpx.codes.TOO_MANY_REQUESTS or status >= 500
    return isinstance(error, httpx.TransportError)


class FrankfurterClient:
    """Client of the Frankfurter v2 API restricted to the ECB reference rates."""

    def __init__(
        self, http: httpx.AsyncClient, *, attempts: int = 5, backoff_seconds: float = 1.0
    ) -> None:
        self._http = http
        self._attempts = attempts
        self._backoff_seconds = backoff_seconds

    async def currencies(self) -> dict[str, str]:
        path = "/v2/currencies"
        content = await self._get(path, {"providers": "ECB"})
        return {row.iso_code: row.name for row in _validate(_CURRENCY_ROWS, content, path)}

    async def eur_rates(self, start: dt.date, end: dt.date) -> list[Rate]:
        params = {
            "providers": "ECB",
            "base": ECB_PIVOT,
            "from": start.isoformat(),
            "to": end.isoformat(),
        }
        path = "/v2/rates"
        content = await self._get(path, params)
        return [
            Rate(row.base, row.quote, row.date, row.rate)
            for row in _validate(_RATE_ROWS, content, path)
        ]

    async def _get(self, path: str, params: Mapping[str, str]) -> bytes:
        attempt = 1
        while True:
            try:
                response = await self._http.get(path, params=params)
                response.raise_for_status()
                return response.content
            except httpx.HTTPError as error:
                if attempt == self._attempts or not _is_retryable(error):
                    raise UpstreamError(path, f"failed after {attempt} attempt(s)") from error
                delay = self._backoff_seconds * 2 ** (attempt - 1)
                logger.warning("GET %s failed: %s; retry in %.1f s", path, error, delay)
                await asyncio.sleep(delay)
                attempt += 1
