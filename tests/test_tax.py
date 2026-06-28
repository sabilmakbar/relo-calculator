"""Tests for the gross → net (take-home) estimator and its /tax endpoint."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.tax import TAX_RATE_BY_COUNTRY, country_to_tax_rate, estimate_net

client = TestClient(app)


# ── unit: rate lookup + estimation ────────────────────────────────

@pytest.mark.parametrize("country,rate", [
    ("Malaysia", 16),
    ("Singapore", 10),
    ("United Arab Emirates", 0),   # no personal income tax
    ("  saudi arabia  ", 0),
])
def test_country_to_tax_rate(country, rate):
    assert country_to_tax_rate(country) == rate


def test_country_to_tax_rate_unknown():
    assert country_to_tax_rate("Atlantis") is None
    assert country_to_tax_rate("") is None
    assert country_to_tax_rate(None) is None


def test_estimate_net_math():
    est = estimate_net(10000, "Malaysia")          # 16%
    assert est["rate"] == 16
    assert est["tax"] == pytest.approx(1600)
    assert est["net"] == pytest.approx(8400)


def test_estimate_net_zero_tax_country():
    est = estimate_net(10000, "Qatar")
    assert est["tax"] == 0
    assert est["net"] == 10000


def test_estimate_net_unknown_country():
    est = estimate_net(10000, "Atlantis")
    assert est["rate"] is None and est["net"] is None


def test_all_rates_are_sane_percentages():
    for rate in TAX_RATE_BY_COUNTRY.values():
        assert 0 <= rate <= 60


# ── endpoint ──────────────────────────────────────────────────────

def test_tax_page_renders():
    r = client.get("/tax")
    assert r.status_code == 200
    assert "Take-Home Pay Estimator" in r.text
    assert '<select id="country"' in r.text


def test_tax_head_ok():
    assert client.head("/tax").status_code == 200


def test_tax_post_computes_net():
    r = client.post("/tax", data={"country": "Malaysia", "gross": "10,000"})
    assert r.status_code == 200
    assert "8,400" in r.text          # net after 16%
    assert "MYR" in r.text            # currency inferred


def test_tax_post_unknown_country_shows_message():
    r = client.post("/tax", data={"country": "Narnia", "gross": "5000"})
    assert r.status_code == 200
    assert "No tax-rate estimate available" in r.text


def test_tax_post_rejects_bad_gross():
    r = client.post("/tax", data={"country": "Malaysia", "gross": "abc"})
    assert "must be a number" in r.text


def test_tax_post_rejects_nonpositive():
    r = client.post("/tax", data={"country": "Malaysia", "gross": "0"})
    assert "positive number" in r.text


def test_main_page_links_to_tax():
    assert 'href="/tax"' in client.get("/").text
