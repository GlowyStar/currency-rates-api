import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rate:
    base: str
    quote: str
    date: dt.date
    value: float
