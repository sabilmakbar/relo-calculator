"""Shared fixtures: sample Numbeo HTML and Yahoo Finance JSON payloads."""
import pytest

from app import data_sources as _ds
from app import fx as _fx


@pytest.fixture(autouse=True)
def _clear_spot_cache():
    """Keep the per-process spot cache from leaking between tests."""
    _fx._SPOT_CACHE.clear()
    yield
    _fx._SPOT_CACHE.clear()


@pytest.fixture(autouse=True)
def _empty_numbeo_cache(monkeypatch):
    """Default to an empty Numbeo cache so tests exercise the live scrape path.
    Cache-specific tests populate _ds._CACHE explicitly."""
    monkeypatch.setattr(_ds, "_CACHE", {})


@pytest.fixture
def numbeo_html():
    """Minimal Numbeo compare_cities page with the diff table we parse."""
    return """
    <html><body>
      <table class="table_indices_diff">
        <tr><td>Cost of Living in <span class="city1">Singapore</span> is 134.5% higher than in <span class="city2">Kuala Lumpur</span></td></tr>
        <tr><td>Rent Prices in Singapore are 409.4% higher than in Kuala Lumpur</td></tr>
      </table>
    </body></html>
    """


@pytest.fixture
def numbeo_html_lower():
    """Variant where destination is cheaper (lower direction)."""
    return """
    <html><body>
      <table class="table_indices_diff">
        <tr><td>Cost of Living in <span class="city1">Penang</span> is 12.0% lower than in <span class="city2">Kuala Lumpur</span></td></tr>
        <tr><td>Rent Prices in Penang are 25.5% lower than in Kuala Lumpur</td></tr>
      </table>
    </body></html>
    """


@pytest.fixture
def numbeo_html_unknown_city():
    """What Numbeo returns when a city can't be found."""
    return "<html><body><h1>Our system cannot find city Atlantis, Malaysia.</h1></body></html>"


@pytest.fixture
def numbeo_html_no_table():
    """A page with no diff table (e.g. layout changed)."""
    return "<html><body><p>Some unrelated content.</p></body></html>"


def _make_yahoo_payload(prices):
    """Build a Yahoo Finance chart JSON response from a list of closes."""
    return {
        "chart": {
            "result": [
                {"indicators": {"quote": [{"close": prices}]}}
            ],
            "error": None,
        }
    }


@pytest.fixture
def yahoo_payload():
    """200 trading days of a gently trending rate around 0.30 (MYR/SGD-ish)."""
    prices = [0.30 + 0.0002 * i for i in range(200)]
    return _make_yahoo_payload(prices)


@pytest.fixture
def yahoo_payload_short():
    """Too few data points — should trigger a validation error."""
    return _make_yahoo_payload([0.30, 0.31, 0.30, 0.29, 0.30])


@pytest.fixture
def yahoo_payload_empty():
    """Yahoo returns an error / no result."""
    return {"chart": {"result": None, "error": {"description": "No data found"}}}


@pytest.fixture
def make_yahoo_payload():
    """Factory so tests can build custom price series."""
    return _make_yahoo_payload


def _make_frankfurter_payload(values, symbol="SGD"):
    """Build a Frankfurter time-series response. Keys just need to sort
    chronologically; the parser doesn't validate the date format."""
    rates = {f"2024-{i:04d}": {symbol: v} for i, v in enumerate(values)}
    return {"amount": 1.0, "base": "MYR", "rates": rates}


@pytest.fixture
def frankfurter_payload():
    """200 days of ECB-style daily rates."""
    return _make_frankfurter_payload([0.30 + 0.0002 * i for i in range(200)])


@pytest.fixture
def make_frankfurter_payload():
    return _make_frankfurter_payload
