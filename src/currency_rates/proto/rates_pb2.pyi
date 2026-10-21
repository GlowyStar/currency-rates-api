from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class LatestRequest(_message.Message):
    __slots__ = ("base", "symbols")
    BASE_FIELD_NUMBER: _ClassVar[int]
    SYMBOLS_FIELD_NUMBER: _ClassVar[int]
    base: str
    symbols: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, base: _Optional[str] = ..., symbols: _Optional[_Iterable[str]] = ...) -> None: ...

class LatestResponse(_message.Message):
    __slots__ = ("base", "date", "rates")
    class RatesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: float
        def __init__(self, key: _Optional[str] = ..., value: _Optional[float] = ...) -> None: ...
    BASE_FIELD_NUMBER: _ClassVar[int]
    DATE_FIELD_NUMBER: _ClassVar[int]
    RATES_FIELD_NUMBER: _ClassVar[int]
    base: str
    date: str
    rates: _containers.ScalarMap[str, float]
    def __init__(self, base: _Optional[str] = ..., date: _Optional[str] = ..., rates: _Optional[_Mapping[str, float]] = ...) -> None: ...

class HistoryRequest(_message.Message):
    __slots__ = ("base", "symbol", "start", "end", "max_points")
    BASE_FIELD_NUMBER: _ClassVar[int]
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    END_FIELD_NUMBER: _ClassVar[int]
    MAX_POINTS_FIELD_NUMBER: _ClassVar[int]
    base: str
    symbol: str
    start: str
    end: str
    max_points: int
    def __init__(self, base: _Optional[str] = ..., symbol: _Optional[str] = ..., start: _Optional[str] = ..., end: _Optional[str] = ..., max_points: _Optional[int] = ...) -> None: ...

class HistoryPoint(_message.Message):
    __slots__ = ("date", "rate")
    DATE_FIELD_NUMBER: _ClassVar[int]
    RATE_FIELD_NUMBER: _ClassVar[int]
    date: str
    rate: float
    def __init__(self, date: _Optional[str] = ..., rate: _Optional[float] = ...) -> None: ...
