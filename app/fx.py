import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _USER_AGENT, "Accept": "application/json"}

# Yahoo serves the same data from two hosts; falling back between them helps
# when one is rate-limiting (429).
_HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]
_RETRYABLE = {429, 500, 502, 503, 504}
_BACKOFF = [0.5, 1.0, 0.0]  # seconds before retry N (last attempt: no wait)


class FxUnavailable(Exception):
    """Raised when the FX rate could not be fetched (e.g. persistent 429)."""


def _display_scale(rate: float) -> int:
    """Power-of-1000 multiplier for the base unit so a tiny rate reads nicely.
    Steps in 10^3 (1, 1000, 1e6, …) and only kicks in for very small rates, e.g.
    0.000049 → 1,000,000 so the card shows '1,000,000 IDR = 49 EUR'. A normal
    rate like 0.3174 stays at scale 1."""
    if not rate or rate <= 0 or rate >= 0.01:
        return 1
    scale = 1000
    while rate * scale < 1 and scale < 10**9:
        scale *= 1000
    return scale


def _ema(prices: list, period: int) -> float:
    """Standard EMA over the full price list; period controls responsiveness."""
    k = 2 / (period + 1)
    val = prices[0]
    for p in prices[1:]:
        val = p * k + val * (1 - k)
    return round(val, 6)


async def _request_chart(ticker: str, max_attempts: int = 3) -> dict:
    """GET the Yahoo chart JSON, retrying transient failures across both hosts."""
    params = {"range": "200d", "interval": "1d"}
    last_err = None

    async with httpx.AsyncClient(timeout=15, headers=_HEADERS, follow_redirects=True) as client:
        # Best-effort: seed Yahoo consent cookies (A1/A3) into the session, which
        # reduces 429s. Reusing this client carries the cookie jar to the chart
        # calls below. Failures here are non-fatal.
        try:
            await client.get("https://fc.yahoo.com")
        except Exception:
            pass

        for attempt in range(max_attempts):
            host = _HOSTS[attempt % len(_HOSTS)]
            url = f"https://{host}/v8/finance/chart/{ticker}"
            try:
                r = await client.get(url, params=params)
                if r.status_code in _RETRYABLE:
                    last_err = f"HTTP {r.status_code} from {host}"
                    logger.warning("FX fetch retryable error: %s (attempt %d)", last_err, attempt + 1)
                    await asyncio.sleep(_BACKOFF[min(attempt, len(_BACKOFF) - 1)])
                    continue
                r.raise_for_status()
                return r.json()
            except httpx.RequestError as e:
                last_err = str(e)
                logger.warning("FX fetch network error: %s (attempt %d)", e, attempt + 1)
                await asyncio.sleep(_BACKOFF[min(attempt, len(_BACKOFF) - 1)])

    raise FxUnavailable(f"Could not fetch FX data after {max_attempts} attempts: {last_err}")


def _parse_yahoo(payload: dict) -> list:
    """Extract the non-null daily closes from a Yahoo chart payload."""
    result = payload.get("chart", {}).get("result")
    if not result:
        err = payload.get("chart", {}).get("error", {}).get("description", "unknown error")
        raise ValueError(f"Yahoo returned no data: {err}")
    closes = result[0]["indicators"]["quote"][0]["close"]
    return [c for c in closes if c is not None]


async def _request_frankfurter_series(from_cur: str, to_cur: str) -> list:
    """Daily series from Frankfurter (ECB reference rates) — free, no API key.

    Covers ~30 major currencies (incl. MYR, SGD). Used as a fallback when Yahoo
    is unavailable (e.g. rate-limited).
    """
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=300)  # ~200 business days
    url = f"https://api.frankfurter.dev/v1/{start}..{end}"

    async with httpx.AsyncClient(timeout=15, headers={"Accept": "application/json"}) as client:
        r = await client.get(url, params={"base": from_cur.upper(), "symbols": to_cur.upper()})
    r.raise_for_status()

    rates = r.json().get("rates", {})
    sym = to_cur.upper()
    # Keys are ISO dates; sort chronologically and pull the target symbol.
    return [rates[d][sym] for d in sorted(rates) if sym in rates[d] and rates[d][sym] is not None]


# Spot-rate cache: (base, sym) -> (rate, expiry). Reversible — a cached A→B
# also answers B→A as 1/rate. In-memory, per-process, short TTL.
_SPOT_CACHE: dict = {}
_SPOT_TTL = 3600  # seconds


def _spot_cache_get(base: str, sym: str):
    now = time.time()
    direct = _SPOT_CACHE.get((base, sym))
    if direct and direct[1] > now:
        return direct[0]
    rev = _SPOT_CACHE.get((sym, base))
    if rev and rev[1] > now and rev[0]:
        return 1.0 / rev[0]
    return None


def _spot_cache_put(base: str, sym: str, rate: float):
    _SPOT_CACHE[(base, sym)] = (rate, time.time() + _SPOT_TTL)


async def _request_spot(from_cur: str, to_cur: str):
    """Latest spot rate from the free, keyless fawazahmed0 currency-api (CDN).

    Broad coverage (~150 currencies incl. Gulf currencies like SAR that ECB
    omits), but no usable daily history — so it's a last resort that yields a
    single point (spot only, no EMA forecast). Results are cached (reversibly)
    for an hour. Returns the rate or None.
    """
    base, sym = from_cur.lower(), to_cur.lower()

    cached = _spot_cache_get(base, sym)
    if cached is not None:
        logger.info("Spot cache hit for %s/%s", base.upper(), sym.upper())
        return cached

    urls = [
        f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{base}.json",
        f"https://latest.currency-api.pages.dev/v1/currencies/{base}.json",
    ]
    async with httpx.AsyncClient(timeout=15, headers={"Accept": "application/json"}) as client:
        for url in urls:
            try:
                r = await client.get(url)
                r.raise_for_status()
                rate = r.json().get(base, {}).get(sym)
                if rate:
                    _spot_cache_put(base, sym, float(rate))
                    return float(rate)
            except (httpx.HTTPError, ValueError, KeyError):
                continue
    return None


async def _load_prices(from_cur: str, to_cur: str, pair: str):
    """Try Yahoo, then Frankfurter, then a spot-only source. Returns (prices, source)."""
    ticker = f"{from_cur.upper()}{to_cur.upper()}=X"

    # 1. Yahoo Finance (intraday-grade history, broadest currency coverage)
    try:
        prices = _parse_yahoo(await _request_chart(ticker))
        if len(prices) >= 10:
            return prices, "Yahoo Finance"
        logger.warning("Yahoo returned too few points for %s; trying fallback", pair)
    except (FxUnavailable, ValueError) as e:
        logger.warning("Yahoo unavailable for %s (%s); trying Frankfurter", pair, e)

    # 2. Frankfurter / ECB (free, no key, history, but only ~31 currencies)
    try:
        prices = await _request_frankfurter_series(from_cur, to_cur)
        if len(prices) >= 10:
            return prices, "Frankfurter (ECB)"
        logger.warning("Frankfurter returned too few points for %s; trying spot", pair)
    except httpx.HTTPError as e:
        logger.warning("Frankfurter unavailable for %s (%s); trying spot", pair, e)

    # 3. Spot-only fallback (broad coverage, no history → no EMA forecast)
    rate = await _request_spot(from_cur, to_cur)
    if rate:
        logger.info("Using spot-only rate for %s", pair)
        return [rate], "Spot rate (no forecast)"

    raise FxUnavailable(f"All FX sources failed for {pair}")


# Blend weights for the next-month forecast: long trend dominates, with the
# faster EMAs adding recent responsiveness.
_BLEND = {"ema_30": 0.3, "ema_90": 0.3, "ema_180": 0.4}


async def fetch_fx_ema(from_currency: str, to_currency: str) -> dict:
    """
    Fetch ~200 days of daily rates (Yahoo Finance, falling back to Frankfurter/ECB)
    and return a blended next-month rate forecast plus its component EMAs.

    Forecast = 0.3·EMA-30 + 0.3·EMA-90 + 0.4·EMA-180 (renormalised over whatever
    horizons the available history supports). EMA-30 is the most responsive,
    EMA-180 the long-term anchor.

    Raises FxUnavailable if every source fails.
    """
    pair = f"{from_currency.upper()}/{to_currency.upper()}"

    prices, source = await _load_prices(from_currency, to_currency, pair)
    n = len(prices)
    logger.info("Fetched %d days of %s FX data from %s", n, pair, source)

    ema_30  = _ema(prices, 30)  if n >= 30  else None
    ema_90  = _ema(prices, 90)  if n >= 90  else None
    ema_180 = _ema(prices, 180) if n >= 180 else None

    available = [(k, v) for k, v in
                 (("ema_30", ema_30), ("ema_90", ema_90), ("ema_180", ema_180)) if v is not None]
    ema_vals = [v for _, v in available]

    # Weighted blend, renormalised over the EMAs we actually have.
    # No EMAs (spot-only source) → forecast is just the latest rate.
    spot_only = not available
    if available:
        wsum = sum(_BLEND[k] for k, _ in available)
        forecast = round(sum(v * _BLEND[k] for k, v in available) / wsum, 6)
    else:
        forecast = round(prices[-1], 6)

    # Trend direction of the forecast vs the longest-horizon anchor available.
    anchor = ema_180 or ema_90 or ema_30 or forecast
    trend = "up" if forecast > anchor else "down" if forecast < anchor else "flat"

    # Band = spread across available EMAs; falls back to the forecast itself.
    band_low = round(min(ema_vals), 6) if ema_vals else forecast
    band_high = round(max(ema_vals), 6) if ema_vals else forecast

    return {
        "pair": pair,
        "source": source,
        "latest": round(prices[-1], 6),
        "ema_30": ema_30,
        "ema_90": ema_90,
        "ema_180": ema_180,
        "forecast": forecast,
        "trend": trend,
        "spot_only": spot_only,
        "scale": _display_scale(forecast),
        "band_low": band_low,
        "band_high": band_high,
        "days": n,
    }
