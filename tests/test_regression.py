"""Regression tests — each pins a specific bug we fixed, so it can't return.

Reference the symptom in the test name/docstring so a future failure is
self-explanatory.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.model import calculate_stats

client = TestClient(app)

COL = {"valuePct": 134.5, "direction": "higher"}
RENT = {"valuePct": 409.4, "direction": "higher"}


async def fake_diffs(country1, city1, country2, city2):
    return {"city_from": city1, "city_to": city2,
            "col_excl_rent": COL, "rent": RENT}


async def fake_fx(from_currency, to_currency):
    return {"pair": f"{from_currency}/{to_currency}", "source": "Yahoo Finance",
            "latest": 0.30, "ema_30": 0.30, "ema_90": 0.30, "ema_180": 0.30,
            "forecast": 0.30, "trend": "flat", "spot_only": False, "scale": 1,
            "band_low": 0.30, "band_high": 0.30, "days": 200}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr("app.main.get_percentage_diff", fake_diffs)
    monkeypatch.setattr("app.main.fetch_fx_ema", fake_fx)


def test_currency_scale_bug_no_nonsensical_negative_savings():
    """Was: destination costs computed on home-currency scale while net_new was
    in destination currency → wildly negative savings. Now FX-scaled."""
    m = calculate_stats(16000, 24000, COL, RENT, fx_rate=0.30, w_rent=0.2, w_non_rent=0.6)
    assert m["rent_new"] == pytest.approx(0.2 * (16000 * 0.30) * (1 + 4.094))
    assert m["savings_new"] > 0  # not a huge negative number


def test_bare_except_no_longer_swallows_errors(patched, monkeypatch):
    """Was: a single bare `except` hid all failures. Now ValueErrors surface
    as user-facing messages."""
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Singapore", "city2": "Singapore",
        "net_home": "16000",  # neither net_new nor increment_pct
    })
    assert "Provide either a new net salary" in r.text


def test_thousand_separated_salary_input_is_accepted(patched):
    """Was: switching salary inputs to formatted text broke float parsing.
    Now commas are stripped server-side."""
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Malaysia", "city2": "Penang",
        "net_home": "16,000", "net_new": "18,500",
    })
    assert r.status_code == 200
    assert "Monthly savings estimate" in r.text


def test_get_compare_redirects_not_405():
    """Was: GET /compare returned 405. Now redirects home."""
    r = client.get("/compare", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/relo"


def test_multiword_names_title_cased_not_lowercased(patched):
    """Was: .capitalize() turned 'Kuala Lumpur' into 'Kuala lumpur'. Now .title()."""
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "kuala lumpur",
        "country2": "Malaysia", "city2": "george town",
        "net_home": "10000", "net_new": "11000",
    })
    assert r.status_code == 200
    # city_from echoes the (title-cased) submitted city
    assert "Kuala Lumpur" in r.text
    assert "Kuala lumpur" not in r.text


def test_savings_pct_delta_uses_home_equivalent():
    """Was: % delta mixed currencies. Now compares home-currency equivalents."""
    m = calculate_stats(10000, 10000, COL, RENT, fx_rate=1.0)
    # same salary, pricier destination → fewer savings → negative delta
    assert m["savings_pct_delta"] < 0
