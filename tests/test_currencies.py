"""Tests for country → ISO 4217 currency inference."""
import json

import pytest

from app.currencies import (
    CURRENCY_BY_COUNTRY,
    CURRENCY_BY_COUNTRY_JSON,
    CAPITAL_BY_COUNTRY,
    country_to_currency,
    country_to_capital,
)


@pytest.mark.parametrize(
    "country,expected",
    [
        ("Malaysia", "MYR"),
        ("Singapore", "SGD"),
        ("United States", "USD"),
        ("United Kingdom", "GBP"),
        ("Germany", "EUR"),
        ("France", "EUR"),
        ("Japan", "JPY"),
    ],
)
def test_known_countries(country, expected):
    assert country_to_currency(country) == expected


@pytest.mark.parametrize("variant", ["malaysia", "MALAYSIA", "  Malaysia  ", "MaLaYsIa"])
def test_case_and_whitespace_insensitive(variant):
    assert country_to_currency(variant) == "MYR"


@pytest.mark.parametrize("alias,expected", [("USA", "USD"), ("us", "USD"), ("UK", "GBP"), ("UAE", "AED")])
def test_common_aliases(alias, expected):
    assert country_to_currency(alias) == expected


def test_unknown_country_returns_none():
    assert country_to_currency("Atlantis") is None
    assert country_to_currency("") is None
    assert country_to_currency(None) is None


def test_eurozone_countries_share_currency():
    # Two different countries, same currency -> caller treats as no-FX.
    assert country_to_currency("France") == country_to_currency("Germany") == "EUR"


def test_json_export_matches_dict():
    assert json.loads(CURRENCY_BY_COUNTRY_JSON) == CURRENCY_BY_COUNTRY


def test_all_codes_are_three_letters():
    for code in CURRENCY_BY_COUNTRY.values():
        assert len(code) == 3 and code.isupper()


@pytest.mark.parametrize("country,capital", [
    ("Malaysia", "Kuala Lumpur"),
    ("Singapore", "Singapore"),
    ("united states", "New York"),
    ("  Japan  ", "Tokyo"),
])
def test_country_to_capital(country, capital):
    assert country_to_capital(country) == capital


def test_country_to_capital_unknown():
    assert country_to_capital("Atlantis") is None
    assert country_to_capital("") is None
    assert country_to_capital(None) is None


def test_every_dropdown_country_has_a_capital():
    # Canonical (non-alias) countries should all resolve to a capital.
    for name in CAPITAL_BY_COUNTRY:
        assert country_to_capital(name)
