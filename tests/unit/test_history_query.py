import datetime as dt

import pytest

from currency_rates.storage.clickhouse import build_history_query

START = dt.date(2025, 1, 1)


def _bucket_days_for(days: int, max_points: int) -> int:
    end = START + dt.timedelta(days=days - 1)
    return build_history_query(
        "USD", "EUR", start=START, end=end, max_points=max_points
    ).bucket_days


@pytest.mark.parametrize(
    ("days", "max_points", "bucket_days"),
    [
        (1, 1, 1),
        (31, 200, 1),
        (200, 200, 1),
        (201, 200, 2),
        (366, 200, 2),
        (1000, 7, 143),
        (10, 1, 10),
    ],
)
def test_bucket_width_is_the_smallest_that_fits(
    days: int, max_points: int, bucket_days: int
) -> None:
    assert _bucket_days_for(days, max_points) == bucket_days


def test_bucket_count_never_exceeds_max_points() -> None:
    for days in range(1, 800, 7):
        for max_points in (1, 2, 3, 50, 200, 1000):
            bucket_days = _bucket_days_for(days, max_points)
            # Same arithmetic as the SQL: intDiv(dateDiff('day', start, date), bucket_days).
            buckets = {offset // bucket_days for offset in range(days)}
            assert len(buckets) <= max_points


def test_values_are_bound_as_parameters_not_formatted_into_sql() -> None:
    end = dt.date(2025, 12, 31)

    query = build_history_query("USD", "EUR", start=START, end=end, max_points=100)

    assert query.parameters == {
        "base": "USD",
        "quote": "EUR",
        "start": START,
        "end": end,
        "bucket_days": 4,
    }
    assert "USD" not in query.sql
    assert "2025" not in query.sql
