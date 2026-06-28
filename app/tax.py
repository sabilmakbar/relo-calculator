"""Rough gross → net (take-home) estimation from an offline effective-rate table.

`app/data/tax_rates.json` holds a `_meta` block (metric + cited sources) and a
`rates` map of lowercase country name → APPROXIMATE effective tax rate (income
tax + employee social contributions, %) for a single average earner.

Provenance: OECD members align with the OECD "net personal average tax rate"
(Taxing Wages); zero-income-tax jurisdictions per PwC; others are representative
rates rounded from public tax summaries. See `_meta.sources` in the JSON. These
are indicative, not payroll-grade — real take-home varies with income, deductions
and household.
"""
import json
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data"

with open(_DATA / "tax_rates.json", encoding="utf-8") as f:
    _RAW = json.load(f)

TAX_META = _RAW.get("_meta", {})
TAX_RATE_BY_COUNTRY = _RAW["rates"]


def country_to_tax_rate(country: str):
    """Approximate effective tax rate (%) for a country, or None if unknown."""
    if not country:
        return None
    return TAX_RATE_BY_COUNTRY.get(country.strip().lower())


def estimate_net(gross: float, country: str) -> dict:
    """Estimate monthly take-home from gross using the country's effective rate."""
    rate = country_to_tax_rate(country)
    if rate is None:
        return {"country": country, "gross": gross, "rate": None, "tax": None, "net": None}
    tax = gross * rate / 100
    return {
        "country": country,
        "gross": gross,
        "rate": rate,
        "tax": tax,
        "net": gross - tax,
    }
