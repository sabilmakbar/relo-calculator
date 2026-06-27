"""Integration tests — exercise the full request → model → render pipeline.

External I/O (Numbeo scrape, Yahoo/Frankfurter FX) is monkeypatched at the
import site so these run offline, but everything else (validation, currency
inference, the savings model, template rendering) runs for real.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

HIGHER_COL = {"valuePct": 134.5, "direction": "higher"}
HIGHER_RENT = {"valuePct": 409.4, "direction": "higher"}


async def fake_diffs(country1, city1, country2, city2):
    return {"city_from": city1, "city_to": city2,
            "col_excl_rent": HIGHER_COL, "rent": HIGHER_RENT}


def fake_fx(rate=0.30, spot_only=False):
    async def _fx(from_currency, to_currency):
        return {
            "pair": f"{from_currency}/{to_currency}", "source": "Yahoo Finance",
            "latest": rate, "ema_30": rate, "ema_90": rate, "ema_180": rate,
            "forecast": rate, "trend": "flat", "spot_only": spot_only,
            "scale": 1, "band_low": rate, "band_high": rate, "days": 200,
        }
    return _fx


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr("app.main.get_percentage_diff", fake_diffs)
    monkeypatch.setattr("app.main.fetch_fx_ema", fake_fx())


def test_cross_country_end_to_end_numbers(patched):
    # Explicit weights so the arithmetic is deterministic:
    # savings 20% → rent 25% of the remaining 80% = 20% of income, other = 60%.
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Singapore", "city2": "Singapore",
        "net_home": "16000", "net_new": "24000",
        "savings_ratio": "20", "rent_share": "25",
    })
    assert r.status_code == 200
    t = r.text
    # Home buckets (MYR): rent 0.2*16000=3,200 · other 0.6*16000=9,600 · savings 3,200
    assert "9,600" in t
    # Destination rent (SGD): 0.2 * (16000*0.30) * (1+4.094) = 4,890
    assert "4,890" in t
    # Destination savings (SGD): 24000 - 4890.24 - 6753.6 = 12,356
    assert "12,356" in t
    assert "breakdown-table" in t


def test_same_country_skips_fx_and_currency_section(patched):
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Malaysia", "city2": "Penang",
        "net_home": "10000", "net_new": "12000",
    })
    assert r.status_code == 200
    assert "FX forecast" not in r.text       # no FX card same-country
    assert "Monthly savings estimate" in r.text


def test_savings_target_endpoint_roundtrip(patched):
    # Ask for +25% savings; the derived salary should reproduce ~+25%.
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Singapore", "city2": "Singapore",
        "net_home": "16000", "increment_pct": "25",
        "savings_ratio": "20", "rent_share": "25",
    })
    assert r.status_code == 200
    assert "To save" in r.text and "destination salary" in r.text
    assert "+25.0% savings" in r.text


def test_spot_only_pair_still_computes(monkeypatch):
    monkeypatch.setattr("app.main.get_percentage_diff", fake_diffs)
    monkeypatch.setattr("app.main.fetch_fx_ema", fake_fx(rate=0.000252, spot_only=True))
    r = client.post("/compare", data={
        "country1": "Indonesia", "city1": "Jakarta",
        "country2": "Saudi arabia", "city2": "Riyadh",
        "net_home": "25000000", "net_new": "7500",
    })
    assert r.status_code == 200
    assert "FX rate · spot" in r.text          # spot card, no forecast
    assert "Monthly savings estimate" in r.text  # savings still computed


def test_fx_failure_keeps_cost_indices(monkeypatch):
    from app.fx import FxUnavailable

    async def boom(*a, **k):
        raise FxUnavailable("rate limited")

    monkeypatch.setattr("app.main.get_percentage_diff", fake_diffs)
    monkeypatch.setattr("app.main.fetch_fx_ema", boom)
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Singapore", "city2": "Singapore",
        "net_home": "16000", "net_new": "24000",
    })
    assert r.status_code == 200
    assert "Cost of living indices" in r.text
    assert "could not be retrieved" in r.text
    assert "Monthly savings estimate" not in r.text
