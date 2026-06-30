"""Tests for Numbeo scraping and parsing."""
import httpx
import pytest
import respx

from app.data_sources import (
    HOME_URL,
    URL_BASE,
    extract_city_differences,
    get_percentage_diff,
)


def _mock_home():
    """Register the browser-mimicking warm-up GET (cookie priming)."""
    respx.get(HOME_URL).mock(return_value=httpx.Response(200))


async def _noop_sleep(_seconds):
    """Stand-in for asyncio.sleep so retry tests don't wait on real backoff."""
    return None


# ── extract_city_differences (pure) ───────────────────────────────

def test_extract_higher():
    out = extract_city_differences("Cost of Living in X is 134.5% higher than in Y")
    assert out == {"valuePct": 134.5, "direction": "higher"}


def test_extract_lower_is_negative():
    out = extract_city_differences("Rent Prices in X are 25.5% lower than in Y")
    assert out["valuePct"] == -25.5
    assert out["direction"] == "lower"


def test_extract_unparseable_raises():
    with pytest.raises(ValueError, match="Could not parse"):
        extract_city_differences("totally unrelated text")


# ── get_percentage_diff (mocked HTTP) ─────────────────────────────

@respx.mock
async def test_get_percentage_diff_success(numbeo_html):
    _mock_home()
    respx.get(url__startswith=URL_BASE).mock(return_value=httpx.Response(200, text=numbeo_html))
    out = await get_percentage_diff("Malaysia", "Kuala Lumpur", "Singapore", "Singapore")
    assert out["col_excl_rent"] == {"valuePct": 134.5, "direction": "higher"}
    assert out["rent"] == {"valuePct": 409.4, "direction": "higher"}
    assert out["city_from"] == "Kuala Lumpur"
    assert out["city_to"] == "Singapore"


@respx.mock
async def test_get_percentage_diff_lower(numbeo_html_lower):
    _mock_home()
    respx.get(url__startswith=URL_BASE).mock(return_value=httpx.Response(200, text=numbeo_html_lower))
    out = await get_percentage_diff("Malaysia", "Kuala Lumpur", "Malaysia", "Penang")
    assert out["col_excl_rent"]["valuePct"] == -12.0
    assert out["rent"]["valuePct"] == -25.5


@respx.mock
async def test_unknown_city_raises_valueerror(numbeo_html_unknown_city):
    _mock_home()
    respx.get(url__startswith=URL_BASE).mock(return_value=httpx.Response(200, text=numbeo_html_unknown_city))
    with pytest.raises(ValueError, match="doesn't recognise"):
        await get_percentage_diff("Malaysia", "Atlantis", "Singapore", "Singapore")


@respx.mock
async def test_missing_table_raises_runtimeerror(numbeo_html_no_table):
    _mock_home()
    respx.get(url__startswith=URL_BASE).mock(return_value=httpx.Response(200, text=numbeo_html_no_table))
    with pytest.raises(RuntimeError, match="table not found"):
        await get_percentage_diff("Malaysia", "Kuala Lumpur", "Singapore", "Singapore")


@respx.mock
async def test_non_retryable_http_error_propagates():
    _mock_home()
    respx.get(url__startswith=URL_BASE).mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        await get_percentage_diff("Malaysia", "Kuala Lumpur", "Singapore", "Singapore")


@respx.mock
async def test_persistent_503_retries_then_raises(monkeypatch):
    # Don't actually sleep through the backoff during tests.
    monkeypatch.setattr("app.data_sources.asyncio.sleep", _noop_sleep)
    _mock_home()
    route = respx.get(url__startswith=URL_BASE).mock(return_value=httpx.Response(503))
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        await get_percentage_diff("Malaysia", "Kuala Lumpur", "Singapore", "Singapore")
    assert route.call_count == 3  # retried up to _MAX_ATTEMPTS


@respx.mock
async def test_transient_503_then_success(monkeypatch, numbeo_html):
    monkeypatch.setattr("app.data_sources.asyncio.sleep", _noop_sleep)
    _mock_home()
    respx.get(url__startswith=URL_BASE).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, text=numbeo_html)]
    )
    out = await get_percentage_diff("Malaysia", "Kuala Lumpur", "Singapore", "Singapore")
    assert out["col_excl_rent"] == {"valuePct": 134.5, "direction": "higher"}
