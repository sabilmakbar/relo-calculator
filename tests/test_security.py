"""Security tests — input validation, output escaping, and SSRF resistance.

These back the findings recorded in SECURITY_TEST.md.
"""
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

import app.data_sources as ds
from app.data_sources import get_percentage_diff
from app.main import app

client = TestClient(app)


async def fake_diffs(country1, city1, country2, city2):
    return {"city_from": city1, "city_to": city2,
            "col_excl_rent": {"valuePct": 10.0, "direction": "higher"},
            "rent": {"valuePct": 10.0, "direction": "higher"}}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr("app.main.get_percentage_diff", fake_diffs)


# ── Input validation (rejects injection-shaped input) ─────────────

@pytest.mark.parametrize("payload", [
    "<script>alert(1)</script>",
    "'; DROP TABLE x;--",
    "../../etc/passwd",
    "Kuala${IFS}Lumpur",
    "city|whoami",
])
def test_place_field_rejects_dangerous_input(patched, payload):
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": payload,
        "country2": "Singapore", "city2": "Singapore",
        "net_home": "1000", "net_new": "2000",
    })
    assert r.status_code == 200
    assert "invalid characters" in r.text


def test_reflected_input_is_html_escaped(patched):
    # Even though validation rejects it, the echoed value must never appear
    # as live markup — Jinja autoescaping must neutralise it.
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "<script>alert(1)</script>",
        "country2": "Singapore", "city2": "Singapore",
        "net_home": "1000", "net_new": "2000",
    })
    assert "<script>alert(1)</script>" not in r.text     # not raw
    assert "&lt;script&gt;" in r.text                    # escaped form present


def test_numeric_fields_reject_non_numeric(patched):
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Singapore", "city2": "Singapore",
        "net_home": "not-a-number", "net_new": "2000",
    })
    assert r.status_code == 200
    assert "must be a number" in r.text


@pytest.mark.parametrize("field,value", [
    ("savings_ratio", "150"),
    ("savings_ratio", "-5"),
    ("rent_share", "999"),
])
def test_slider_values_bounded(patched, field, value):
    data = {
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Singapore", "city2": "Singapore",
        "net_home": "1000", "net_new": "2000",
    }
    data[field] = value
    r = client.post("/compare", data=data)
    assert "between 0 and 100" in r.text


def test_negative_or_zero_salary_rejected(patched):
    r = client.post("/compare", data={
        "country1": "Malaysia", "city1": "Kuala Lumpur",
        "country2": "Singapore", "city2": "Singapore",
        "net_home": "0", "net_new": "2000",
    })
    assert "positive number" in r.text


# ── SSRF resistance — user input never changes the request host ───

async def test_scraper_only_hits_numbeo_host(monkeypatch):
    seen = []

    class _Resp:
        status_code = 200
        text = (
            '<table class="table_indices_diff">'
            "<tr><td>Cost of Living in B is 10.0% higher than in A</td></tr>"
            "<tr><td>Rent Prices in B are 10.0% higher than in A</td></tr></table>"
        )

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, **kwargs):
            seen.append((url, params))
            return _Resp()

    monkeypatch.setattr(ds, "_new_session", lambda: _Session())

    # A hostile "city" value must ride along as a query param — never the host.
    evil = "http://evil.example/@numbeo"
    await get_percentage_diff("Malaysia", evil, "Singapore", "Singapore")

    hosts = {urlparse(url).hostname for url, _ in seen}
    assert hosts == {"www.numbeo.com"}  # every request went to Numbeo, incl. warm-up
    # the malicious input stayed in the params, not the URL
    compare_params = [p for _, p in seen if p]
    assert compare_params and evil in compare_params[0].values()


def test_missing_required_field_is_handled(patched):
    # Omitting a required field → FastAPI 422, not a 500/stack trace.
    r = client.post("/compare", data={"country1": "Malaysia"})
    assert r.status_code == 422
