import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rate:
    base: str
    quote: str
    date: dt.date
    value: float


@dataclass(frozen=True, slots=True)
class LatestRates:
    base: str
    date: dt.date
    rates: dict[str, float]
