"""Tests for the FX EMA prediction module."""
import httpx
import pytest
import respx

from app.fx import _display_scale, _ema, fetch_fx_ema, FxUnavailable


@pytest.mark.parametrize("rate,scale", [
    (0.3174, 1),        # normal rate — no scaling
    (1.5, 1),
    (0.05, 1),          # still readable
    (0.0049, 1000),     # tiny → per 1,000
    (0.000049, 1000000),# very tiny → per 1,000,000
    (0, 1),
])
def test_display_scale(rate, scale):
    assert _display_scale(rate) == scale


@pytest.fixture
def no_backoff(monkeypatch):
    """Skip real sleeps during retry tests."""
    async def _instant(_seconds):
        return None
    monkeypatch.setattr("app.fx.asyncio.sleep", _instant)

YAHOO_HOST = r"query\d\.finance\.yahoo\.com"


# ── _ema (pure) ───────────────────────────────────────────────────

def test_ema_constant_series():
    # EMA of a flat series equals that constant.
    assert _ema([5.0] * 50, 30) == pytest.approx(5.0)


def test_ema_responsiveness():
    # Shorter period reacts more to a recent jump than a longer period.
    prices = [1.0] * 50 + [2.0] * 5
    fast = _ema(prices, 10)
    slow = _ema(prices, 50)
    assert fast > slow  # fast EMA closer to the new level


# ── fetch_fx_ema (mocked Yahoo) ───────────────────────────────────

FRANKFURTER = r"https://api\.frankfurter\.dev/.*"
SPOT = r".*currency-api.*"


@respx.mock
async def test_fetch_fx_ema_success(yahoo_payload):
    respx.get(url__regex=rf"https://{YAHOO_HOST}.*").mock(
        return_value=httpx.Response(200, json=yahoo_payload)
    )
    out = await fetch_fx_ema("MYR", "SGD")
    assert out["pair"] == "MYR/SGD"
    assert out["source"] == "Yahoo Finance"
    assert out["days"] == 200
    assert out["ema_30"] is not None
    assert out["ema_90"] is not None
    assert out["ema_180"] is not None
    assert out["forecast"] is not None
    assert out["trend"] in {"up", "down", "flat"}
    # band brackets all three EMAs
    assert out["band_low"] <= min(out["ema_30"], out["ema_90"], out["ema_180"])
    assert out["band_high"] >= max(out["ema_30"], out["ema_90"], out["ema_180"])
    # forecast is the renormalised 0.3/0.3/0.4 blend
    expected = 0.3 * out["ema_30"] + 0.3 * out["ema_90"] + 0.4 * out["ema_180"]
    assert out["forecast"] == pytest.approx(expected, abs=1e-6)


@respx.mock
async def test_fetch_fx_ema_uppercases_ticker(yahoo_payload):
    route = respx.get(url__regex=rf"https://{YAHOO_HOST}.*").mock(
        return_value=httpx.Response(200, json=yahoo_payload)
    )
    await fetch_fx_ema("myr", "sgd")
    called_url = str(route.calls.last.request.url)
    assert "MYRSGD=X" in called_url


@respx.mock
async def test_falls_back_to_frankfurter_on_empty_yahoo(yahoo_payload_empty, frankfurter_payload):
    respx.get(url__regex=rf"https://{YAHOO_HOST}.*").mock(
        return_value=httpx.Response(200, json=yahoo_payload_empty)
    )
    respx.get(url__regex=FRANKFURTER).mock(return_value=httpx.Response(200, json=frankfurter_payload))
    out = await fetch_fx_ema("MYR", "SGD")
    assert out["source"] == "Frankfurter (ECB)"
    assert out["days"] == 200
    assert out["ema_30"] is not None


@respx.mock
async def test_history_too_few_falls_back_to_spot(yahoo_payload_short, make_frankfurter_payload):
    # Both history sources too short → spot-only fallback yields a single point.
    respx.get(url__regex=rf"https://{YAHOO_HOST}.*").mock(
        return_value=httpx.Response(200, json=yahoo_payload_short)
    )
    respx.get(url__regex=FRANKFURTER).mock(
        return_value=httpx.Response(200, json=make_frankfurter_payload([0.30, 0.31, 0.30]))
    )
    respx.get(url__regex=SPOT).mock(return_value=httpx.Response(200, json={"myr": {"sgd": 0.305}}))
    out = await fetch_fx_ema("MYR", "SGD")
    assert out["source"] == "Spot rate (no forecast)"
    assert out["spot_only"] is True
    assert out["forecast"] == 0.305
    assert out["ema_30"] is None
    assert out["band_low"] == out["band_high"] == 0.305


@respx.mock
async def test_retries_on_429_then_succeeds(yahoo_payload, no_backoff):
    # First host returns 429, retry on the second host succeeds.
    respx.get(url__regex=rf"https://{YAHOO_HOST}.*").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json=yahoo_payload),
        ]
    )
    out = await fetch_fx_ema("MYR", "SGD")
    assert out["pair"] == "MYR/SGD"
    assert out["ema_30"] is not None


@respx.mock
async def test_falls_back_to_frankfurter_on_429(frankfurter_payload, no_backoff):
    # Yahoo persistently rate-limited → fall back to Frankfurter (ECB).
    respx.get(url__regex=rf"https://{YAHOO_HOST}.*").mock(return_value=httpx.Response(429))
    respx.get(url__regex=FRANKFURTER).mock(return_value=httpx.Response(200, json=frankfurter_payload))
    out = await fetch_fx_ema("MYR", "SGD")
    assert out["source"] == "Frankfurter (ECB)"
    assert out["ema_30"] is not None


@respx.mock
async def test_falls_back_to_spot_when_no_history(no_backoff):
    # Yahoo 429, Frankfurter 404 (unsupported currency) → spot-only rate.
    respx.get(url__regex=rf"https://{YAHOO_HOST}.*").mock(return_value=httpx.Response(429))
    respx.get(url__regex=FRANKFURTER).mock(return_value=httpx.Response(404))
    respx.get(url__regex=SPOT).mock(return_value=httpx.Response(200, json={"idr": {"sar": 0.000252}}))
    out = await fetch_fx_ema("IDR", "SAR")
    assert out["source"] == "Spot rate (no forecast)"
    assert out["spot_only"] is True
    assert out["forecast"] == 0.000252
    assert out["trend"] == "flat"


@respx.mock
async def test_all_sources_fail_raises(no_backoff):
    respx.get(url__regex=rf"https://{YAHOO_HOST}.*").mock(return_value=httpx.Response(429))
    respx.get(url__regex=FRANKFURTER).mock(return_value=httpx.Response(503))
    respx.get(url__regex=SPOT).mock(return_value=httpx.Response(404))
    with pytest.raises(FxUnavailable, match="All FX sources failed"):
        await fetch_fx_ema("MYR", "SGD")


@respx.mock
async def test_blend_renormalizes_when_ema180_missing(make_yahoo_payload):
    # ~100 points: EMA-30 & EMA-90 available, EMA-180 missing.
    # Weights 0.3/0.3 should renormalise to 0.5/0.5.
    prices = [0.30 + 0.0005 * i for i in range(100)]
    respx.get(url__regex=rf"https://{YAHOO_HOST}.*").mock(
        return_value=httpx.Response(200, json=make_yahoo_payload(prices))
    )
    out = await fetch_fx_ema("MYR", "SGD")
    assert out["ema_180"] is None
    expected = (0.3 * out["ema_30"] + 0.3 * out["ema_90"]) / 0.6
    assert out["forecast"] == pytest.approx(expected, abs=1e-6)


@respx.mock
async def test_spot_cache_is_reversible(no_backoff):
    # First call A→B fetches and caches; reverse B→A is served as 1/rate
    # from cache without another network hit.
    respx.get(url__regex=rf"https://{YAHOO_HOST}.*").mock(return_value=httpx.Response(429))
    respx.get(url__regex=FRANKFURTER).mock(return_value=httpx.Response(404))
    spot = respx.get(url__regex=SPOT).mock(return_value=httpx.Response(200, json={"idr": {"sar": 0.000252}}))

    fwd = await fetch_fx_ema("IDR", "SAR")
    assert fwd["forecast"] == 0.000252
    calls_after_fwd = spot.call_count

    rev = await fetch_fx_ema("SAR", "IDR")
    assert rev["forecast"] == pytest.approx(1 / 0.000252)
    # Reverse direction served from cache — no extra spot request.
    assert spot.call_count == calls_after_fwd


@respx.mock
async def test_short_window_nulls_longer_emas(make_yahoo_payload):
    # 40 points: EMA-30 available, EMA-60 / EMA-180 should be None.
    prices = [0.30 + 0.001 * i for i in range(40)]
    respx.get(url__regex=rf"https://{YAHOO_HOST}.*").mock(
        return_value=httpx.Response(200, json=make_yahoo_payload(prices))
    )
    out = await fetch_fx_ema("MYR", "SGD")
    assert out["ema_30"] is not None
    assert out["ema_90"] is None
    assert out["ema_180"] is None
    # band falls back to the single available EMA; forecast == EMA-30
    assert out["band_low"] == out["band_high"] == out["ema_30"]
    assert out["forecast"] == out["ema_30"]


@respx.mock
async def test_trend_up_when_forecast_above_ema180(make_yahoo_payload):
    # Rising series → EMA-30 (recent) > EMA-180 (lagging) → forecast above anchor.
    prices = [0.30 + 0.001 * i for i in range(200)]
    respx.get(url__regex=rf"https://{YAHOO_HOST}.*").mock(
        return_value=httpx.Response(200, json=make_yahoo_payload(prices))
    )
    out = await fetch_fx_ema("MYR", "SGD")
    assert out["trend"] == "up"
