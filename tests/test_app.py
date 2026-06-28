"""Endpoint tests for the FastAPI app.

Network functions (Numbeo scrape, Yahoo FX) are monkeypatched at their
import site in app.main, so these tests never touch the network.
"""
import pytest
from fastapi.testclient import TestClient

from app.fx import FxUnavailable
from app.main import app, _fx_fmt

client = TestClient(app)


def test_fx_fmt_adaptive_precision():
    assert _fx_fmt(0.0000730) == "0.00007300"   # tiny rate keeps sig figs
    assert _fx_fmt(0.3174) == "0.3174"
    assert _fx_fmt(13698.6301) == "13,698.63"    # big rate: separators, 2dp
    assert _fx_fmt(None) == "—"


async def fake_diffs(country1, city1, country2, city2):
    return {
        "city_from": city1,
        "city_to": city2,
        "col_excl_rent": {"valuePct": 134.5, "direction": "higher"},
        "rent": {"valuePct": 409.4, "direction": "higher"},
    }


async def fake_fx(from_currency, to_currency):
    return {
        "pair": f"{from_currency}/{to_currency}",
        "source": "Yahoo Finance",
        "latest": 0.30,
        "ema_30": 0.30,
        "ema_90": 0.31,
        "ema_180": 0.33,
        "forecast": 0.315,
        "trend": "down",
        "spot_only": False,
        "scale": 1,
        "band_low": 0.30,
        "band_high": 0.33,
        "days": 200,
    }


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr("app.main.get_percentage_diff", fake_diffs)
    monkeypatch.setattr("app.main.fetch_fx_ema", fake_fx)


# ── GET / ─────────────────────────────────────────────────────────

def test_landing_page_lists_both_tools():
    r = client.get("/")
    assert r.status_code == 200
    assert "Relocation Finance Tools" in r.text
    assert 'href="/relo"' in r.text
    assert 'href="/tax"' in r.text


def test_relo_page_renders():
    r = client.get("/relo")
    assert r.status_code == 200
    assert "inferred from countries" in r.text
    assert "increase % of savings" in r.text
    # From / To each render on their own row
    assert r.text.count('class="loc-row"') == 2
    # Countries are dropdowns populated from the currency map
    assert '<select id="country1"' in r.text
    assert '<option value="Malaysia"' in r.text
    # No country pre-selected, and no pre-filled city/salary defaults
    assert "selected" not in r.text
    assert 'name="city1" form="calc" value=""' in r.text
    assert 'name="net_home" form="calc" value=""' in r.text


# ── POST /compare ─────────────────────────────────────────────────

def test_same_country_no_fx(patched):
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Malaysia", "city2": "Penang",
        "net_home": "10000", "net_new": "11000",
    })
    assert r.status_code == 200
    assert "Monthly savings estimate" in r.text
    # No FX card for same-country (card title only renders cross-country)
    assert "FX prediction · next month" not in r.text


def test_cross_country_with_new_salary(patched):
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Singapore", "city2": "Singapore",
        "net_home": "16000", "net_new": "24000",
    })
    assert r.status_code == 200
    assert "FX forecast · next month" in r.text
    assert "1 MYR =" in r.text
    # trend pill rendered (fake_fx → "down")
    assert 'class="fx-trend down"' in r.text
    # cost breakdown rendered as a comparison table
    assert "breakdown-table" in r.text
    assert "Other living" in r.text


def test_head_root_ok_for_health_check():
    r = client.head("/")
    assert r.status_code == 200


def test_get_compare_redirects_to_form():
    r = client.get("/compare", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/relo"


def test_amounts_use_thousand_separators(patched):
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Singapore", "city2": "Singapore",
        "net_home": "160000", "net_new": "240000",
    })
    assert r.status_code == 200
    assert "160,000" in r.text
    assert "240,000" in r.text


def test_cross_country_with_savings_target(patched):
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Singapore", "city2": "Singapore",
        "net_home": "16000", "increment_pct": "20",
    })
    assert r.status_code == 200
    # The derived-salary note should appear.
    assert "To save" in r.text
    assert "destination salary" in r.text


def test_missing_salary_and_increment_shows_error(patched):
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Singapore", "city2": "Singapore",
        "net_home": "16000",
    })
    assert r.status_code == 200
    assert "Provide either a new net salary" in r.text


def test_invalid_city_characters_shows_error(patched):
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "KL; DROP TABLE",
        "country2": "Singapore", "city2": "Singapore",
        "net_home": "16000", "net_new": "24000",
    })
    assert r.status_code == 200
    assert "invalid characters" in r.text


def test_unknown_country_currency_warning(patched):
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Narnia", "city2": "Cair Paravel",
        "net_home": "16000", "net_new": "24000",
    })
    assert r.status_code == 200
    # Jinja escapes the apostrophe in "Couldn't"; match the unambiguous part.
    assert "determine the currency for Narnia" in r.text
    # No FX prediction card when currency can't be inferred.
    assert "FX prediction · next month" not in r.text


def test_budget_sliders_reflected_in_result(patched):
    # Savings 50%, rent share 50% → of income: rent 25%, living 25%, savings 50%.
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Malaysia", "city2": "Penang",
        "net_home": "10000", "net_new": "11000",
        "savings_ratio": "50", "rent_share": "50",
    })
    assert r.status_code == 200
    assert "25.0% rent" in r.text
    assert "25.0% living costs" in r.text
    assert "50% savings" in r.text


def test_invalid_slider_value_rejected(patched):
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Malaysia", "city2": "Penang",
        "net_home": "10000", "net_new": "11000",
        "savings_ratio": "150",
    })
    assert r.status_code == 200
    assert "between 0 and 100" in r.text


def test_fx_failure_degrades_gracefully(monkeypatch):
    # Numbeo succeeds, but FX is rate-limited: show indices + warning, skip savings.
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
    # Cost indices still shown
    assert "Cost of living indices" in r.text
    # Degradation warning shown, savings + FX cards skipped
    assert "could not be retrieved" in r.text
    assert "Monthly savings estimate" not in r.text
    assert "FX prediction · next month" not in r.text


def test_negative_salary_rejected(patched):
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Malaysia", "city2": "Penang",
        "net_home": "-5000", "net_new": "6000",
    })
    assert r.status_code == 200
    assert "positive number" in r.text
