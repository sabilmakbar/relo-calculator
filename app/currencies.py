"""Country → currency / capital-city lookups, loaded from app/data/*.json.

Keys are lowercase country names (incl. common Numbeo aliases like "usa", "uk").
Used to infer the FX pair and to prefill the city field without asking the user.
"""
import json
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data"


def _load(name: str) -> dict:
    with open(_DATA / name, encoding="utf-8") as f:
        return json.load(f)


CURRENCY_BY_COUNTRY = _load("currency_by_country.json")
CAPITAL_BY_COUNTRY = _load("capital_city.json")

# JSON forms injected into the page (single source of truth — no duplicate JS tables).
CURRENCY_BY_COUNTRY_JSON = json.dumps(CURRENCY_BY_COUNTRY)
CAPITAL_BY_COUNTRY_JSON = json.dumps(CAPITAL_BY_COUNTRY)

# Short aliases we don't want cluttering the dropdown (the canonical name stays).
_ALIASES = {"usa", "us", "uk", "uae", "korea", "czechia"}

# Title-cased, de-duplicated, alphabetically sorted list for the country dropdown.
COUNTRIES = sorted(
    {name.title() for name in CURRENCY_BY_COUNTRY if name not in _ALIASES}
)


def country_to_currency(country: str) -> str | None:
    """Return ISO 4217 code for a country name, or None if unknown."""
    if not country:
        return None
    return CURRENCY_BY_COUNTRY.get(country.strip().lower())


def country_to_capital(country: str) -> str | None:
    """Return the capital/primary city for a country name, or None if unknown."""
    if not country:
        return None
    return CAPITAL_BY_COUNTRY.get(country.strip().lower())
