import datetime as dt

from currency_rates.domain import Rate
from currency_rates.ingestion.worker import derive_cross_rates

DAY = dt.date(2026, 10, 20)


def _by_pair(rates: list[Rate]) -> dict[tuple[str, str], float]:
    return {(rate.base, rate.quote): rate.value for rate in rates}


def test_every_ordered_pair_is_derived_from_eur_rates() -> None:
    eur_rates = [Rate("EUR", "USD", DAY, 1.25), Rate("EUR", "GBP", DAY, 0.8)]

    crosses = _by_pair(derive_cross_rates(eur_rates))

    assert crosses == {
        ("EUR", "USD"): 1.25,
        ("EUR", "GBP"): 0.8,
        ("USD", "EUR"): 0.8,
        ("USD", "GBP"): 0.64,
        ("GBP", "EUR"): 1.25,
        ("GBP", "USD"): 1.5625,
    }


def test_identity_row_from_upstream_adds_no_pair() -> None:
    eur_rates = [Rate("EUR", "EUR", DAY, 1.0), Rate("EUR", "USD", DAY, 1.25)]

    crosses = derive_cross_rates(eur_rates)

    assert {(rate.base, rate.quote) for rate in crosses} == {("EUR", "USD"), ("USD", "EUR")}


def test_days_are_never_mixed() -> None:
    next_day = DAY + dt.timedelta(days=1)
    eur_rates = [Rate("EUR", "USD", DAY, 1.25), Rate("EUR", "USD", next_day, 1.0)]

    crosses = derive_cross_rates(eur_rates)

    usd_to_eur = {rate.date: rate.value for rate in crosses if rate.base == "USD"}
    assert usd_to_eur == {DAY: 0.8, next_day: 1.0}


def test_cross_rates_keep_six_significant_digits() -> None:
    eur_rates = [Rate("EUR", "USD", DAY, 1.1253), Rate("EUR", "IDR", DAY, 20290.28)]

    crosses = _by_pair(derive_cross_rates(eur_rates))

    assert crosses[("USD", "EUR")] == 0.888652
    assert crosses[("IDR", "USD")] == 5.54601e-05
