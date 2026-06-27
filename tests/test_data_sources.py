"""Tests for Numbeo scraping and parsing."""
import httpx
import pytest
import respx

from app.data_sources import (
    URL_BASE,
    extract_city_differences,
    get_percentage_diff,
)


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
    respx.get(url__startswith=URL_BASE).mock(return_value=httpx.Response(200, text=numbeo_html))
    out = await get_percentage_diff("Malaysia", "Kuala Lumpur", "Singapore", "Singapore")
    assert out["col_excl_rent"] == {"valuePct": 134.5, "direction": "higher"}
    assert out["rent"] == {"valuePct": 409.4, "direction": "higher"}
    assert out["city_from"] == "Kuala Lumpur"
    assert out["city_to"] == "Singapore"


@respx.mock
async def test_get_percentage_diff_lower(numbeo_html_lower):
    respx.get(url__startswith=URL_BASE).mock(return_value=httpx.Response(200, text=numbeo_html_lower))
    out = await get_percentage_diff("Malaysia", "Kuala Lumpur", "Malaysia", "Penang")
    assert out["col_excl_rent"]["valuePct"] == -12.0
    assert out["rent"]["valuePct"] == -25.5


@respx.mock
async def test_unknown_city_raises_valueerror(numbeo_html_unknown_city):
    respx.get(url__startswith=URL_BASE).mock(return_value=httpx.Response(200, text=numbeo_html_unknown_city))
    with pytest.raises(ValueError, match="doesn't recognise"):
        await get_percentage_diff("Malaysia", "Atlantis", "Singapore", "Singapore")


@respx.mock
async def test_missing_table_raises_runtimeerror(numbeo_html_no_table):
    respx.get(url__startswith=URL_BASE).mock(return_value=httpx.Response(200, text=numbeo_html_no_table))
    with pytest.raises(RuntimeError, match="table not found"):
        await get_percentage_diff("Malaysia", "Kuala Lumpur", "Singapore", "Singapore")


@respx.mock
async def test_http_error_propagates():
    respx.get(url__startswith=URL_BASE).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await get_percentage_diff("Malaysia", "Kuala Lumpur", "Singapore", "Singapore")
