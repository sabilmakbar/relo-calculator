import asyncio
import logging
import re
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .currencies import (
    country_to_currency,
    COUNTRIES,
    CURRENCY_BY_COUNTRY_JSON,
    CAPITAL_BY_COUNTRY_JSON,
)
from .data_sources import get_percentage_diff
from .fx import fetch_fx_ema, FxUnavailable
from .model import calculate_stats, required_net_new_for_savings_increase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _fx_fmt(x) -> str:
    """Format an FX rate with adaptive precision + thousand separators, so tiny
    rates (e.g. 0.0000730 IDR→SGD) and large ones (13,698.63) both read well."""
    if x is None:
        return "—"
    from math import floor, log10
    ax = abs(x)
    if ax == 0:
        decimals = 2
    else:
        # ~4 significant digits, clamped to a sane range.
        decimals = min(8, max(2, 3 - floor(log10(ax))))
    return f"{x:,.{decimals}f}"


templates.env.filters["fxfmt"] = _fx_fmt

# Allows letters (incl. accented), spaces, hyphens, apostrophes, periods — covers
# names like "Kuala Lumpur", "São Paulo", "St. Louis", "Côte d'Ivoire".
_PLACE_RE = re.compile(r"^[A-Za-zÀ-ÿ\s\-'.]+$")


def _validate_place(label: str, value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{label} must not be empty.")
    if not _PLACE_RE.match(value):
        raise ValueError(f"{label} contains invalid characters: '{value}'")
    return value


def _pct(label: str, value, default: float) -> float:
    """Parse a 0–100 percentage form field, falling back to a default."""
    if value is None or not str(value).strip():
        return default
    v = float(value)
    if not 0 <= v <= 100:
        raise ValueError(f"{label} must be between 0 and 100.")
    return v


def _default_form() -> dict:
    """Initial form field values (echoed back so input persists across submits)."""
    return {
        "country1": "", "city1": "",
        "country2": "", "city2": "",
        "net_home": "", "net_new": "", "increment_pct": "",
        "savings_ratio": "20", "rent_share": "25",
    }


def _num_str(x: float) -> str:
    """Format a number without a trailing .0 for whole values."""
    return str(int(x)) if float(x).is_integer() else str(x)


def _to_amount(label: str, value, required: bool = False):
    """Parse a money field that may contain thousand separators. Returns None
    if blank and not required; raises ValueError on bad input."""
    s = (value or "").replace(",", "").strip()
    if not s:
        if required:
            raise ValueError(f"{label} is required.")
        return None
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"{label} must be a number.")


# GET for browsers, HEAD so platform health checks (e.g. Render) get a 200.
@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def form(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"result": None, "error": None, "ccy_json": CURRENCY_BY_COUNTRY_JSON,
         "cap_json": CAPITAL_BY_COUNTRY_JSON, "form": _default_form(), "countries": COUNTRIES},
    )


@app.get("/compare")
async def compare_get():
    """A direct GET to /compare (refresh, bookmark) has no form data — send home."""
    return RedirectResponse(url="/", status_code=303)


@app.post("/compare", response_class=HTMLResponse)
async def compare(
    request: Request,
    country1: str = Form(...),
    city1: str = Form(...),
    country2: str = Form(...),
    city2: str = Form(...),
    net_home: str = Form(...),
    net_new: (str | None) = Form(None),
    increment_pct: (str | None) = Form(None),
    savings_ratio: (str | None) = Form(None),
    rent_share: (str | None) = Form(None),
):
    error = None
    result = None

    # Echo submitted values back so the form persists across submits
    # (commas stripped; the frontend re-formats them on load).
    form_values = {
        "country1": country1, "city1": city1,
        "country2": country2, "city2": city2,
        "net_home": (net_home or "").replace(",", "").strip(),
        "net_new": (net_new or "").replace(",", "").strip(),
        "increment_pct": (increment_pct or "").strip(),
        "savings_ratio": (savings_ratio or "20").strip(),
        "rent_share": (rent_share or "25").strip(),
    }

    try:
        country1 = _validate_place("Country 1", country1).title()
        city1 = _validate_place("City 1", city1).title()
        country2 = _validate_place("Country 2", country2).title()
        city2 = _validate_place("City 2", city2).title()

        net_home_f = _to_amount("Current net salary", net_home, required=True)
        if net_home_f <= 0:
            raise ValueError("Current net salary must be a positive number.")

        # Budget weights from the sliders (fall back to the classic 30/50/20).
        savings_pct = _pct("Savings rate", savings_ratio, 20.0)
        rent_pct = _pct("Rent share", rent_share, 25.0)
        spend = 1 - savings_pct / 100
        w_rent = spend * rent_pct / 100
        w_non_rent = spend - w_rent

        net_new_raw = _to_amount("New net salary", net_new)
        increment_pct_raw = float((increment_pct or "").replace(",", "").strip()) if (increment_pct or "").strip() else None

        if net_new_raw is None and increment_pct_raw is None:
            raise ValueError("Provide either a new net salary or an expected increase %.")

        # Detect same-country early — determines whether to fetch FX.
        same_country = country1.lower() == country2.lower()

        # Infer currencies from the country names (no manual input).
        cur_home = cur_dest = None
        ccy_warning = None
        if not same_country:
            cur_home = country_to_currency(country1)
            cur_dest = country_to_currency(country2)
            if not cur_home or not cur_dest:
                missing = country1 if not cur_home else country2
                ccy_warning = (
                    f"Couldn't determine the currency for {missing}, so figures are "
                    "shown without FX conversion. Costs may not be directly comparable."
                )
                cur_home = cur_dest = None
            elif cur_home == cur_dest:
                # Different countries, same currency (e.g. two Eurozone states) — no FX needed.
                cur_home = cur_dest = None

        # Fetch Numbeo (essential) and FX (optional) concurrently. An FX failure
        # must NOT abort the comparison — Numbeo cost data is still useful.
        needs_fx = bool(cur_home and cur_dest)

        async def _safe_fx():
            if not needs_fx:
                return None
            try:
                return await fetch_fx_ema(cur_home, cur_dest)
            except (FxUnavailable, httpx.HTTPError, ValueError) as e:
                logger.warning("FX fetch failed for %s/%s: %s", cur_home, cur_dest, e)
                return None

        diffs, fx_data = await asyncio.gather(
            get_percentage_diff(country1, city1, country2, city2),
            _safe_fx(),
        )

        # FX was required for this pair but couldn't be retrieved: show the cost
        # indices, but skip savings (which need currency conversion to be meaningful).
        fx_failed = needs_fx and fx_data is None
        if fx_failed:
            result = {
                "cities": diffs,
                "model": None,
                "fx": None,
                "savings_band": None,
                "equiv_band": None,
                "same_country": same_country,
                "used_increment": False,
                "increment_pct": increment_pct_raw,
                "derived_net_new": None,
                "ccy_warning": None,
                "fx_failed": True,
            }
        else:
            fx_rate = (fx_data["forecast"] or fx_data["latest"]) if fx_data else 1.0

            # Compute net_new in destination currency.
            # FX must be resolved first so the savings target can be converted.
            used_increment = False
            derived_net_new = None

            if net_new_raw is not None:
                net_new_f = net_new_raw      # user entered dest-currency amount directly
            else:
                # increment_pct is a SAVINGS-increase target — back-solve the
                # destination salary needed to grow savings by that %.
                used_increment = True
                net_new_f = required_net_new_for_savings_increase(
                    net_home_f, increment_pct_raw, diffs["col_excl_rent"], diffs["rent"],
                    fx_rate, w_rent=w_rent, w_non_rent=w_non_rent,
                )
                derived_net_new = net_new_f

            if net_new_f <= 0:
                raise ValueError("New net salary must be a positive number.")

            model = calculate_stats(
                net_home_f,
                net_new_f,
                diffs["col_excl_rent"],
                diffs["rent"],
                fx_rate=fx_rate,
                w_rent=w_rent,
                w_non_rent=w_non_rent,
            )

            # Sensitivity band: re-run model at EMA band edges for a savings range.
            savings_band = None
            equiv_band = None
            if fx_data and fx_data["band_low"] != fx_data["band_high"]:
                m_lo = calculate_stats(net_home_f, net_new_f, diffs["col_excl_rent"], diffs["rent"], fx_rate=fx_data["band_low"], w_rent=w_rent, w_non_rent=w_non_rent)
                m_hi = calculate_stats(net_home_f, net_new_f, diffs["col_excl_rent"], diffs["rent"], fx_rate=fx_data["band_high"], w_rent=w_rent, w_non_rent=w_non_rent)
                savings_band = sorted([m_lo["savings_new"], m_hi["savings_new"]])
                equiv_band   = sorted([m_lo["equiv_net_new_for_same_savings"], m_hi["equiv_net_new_for_same_savings"]])

            result = {
                "cities": diffs,
                "model": model,
                "fx": fx_data,
                "savings_band": savings_band,
                "equiv_band": equiv_band,
                "same_country": same_country,
                "used_increment": used_increment,
                "increment_pct": increment_pct_raw,
                "derived_net_new": derived_net_new,
                "ccy_warning": ccy_warning,
                "fx_failed": False,
                "weights": {
                    "savings": savings_pct,
                    "rent": round(w_rent * 100, 1),
                    "non_rent": round(w_non_rent * 100, 1),
                },
            }

    except ValueError as e:
        error = str(e)
    except httpx.HTTPStatusError as e:
        logger.error("Numbeo HTTP error: %s", e)
        error = f"Numbeo returned an error ({e.response.status_code}). Try again later."
    except httpx.RequestError as e:
        logger.error("Numbeo request failed: %s", e)
        error = "Network error contacting Numbeo. Check your connection and try again."
    except RuntimeError as e:
        logger.error("Numbeo parsing error: %s", e)
        error = f"Could not parse Numbeo data: {e}. The site layout may have changed."
    except Exception as e:
        logger.exception("Unexpected error in /compare")
        error = "An unexpected error occurred. Please try again."

    return templates.TemplateResponse(
        request,
        "index.html",
        {"result": result, "error": error, "ccy_json": CURRENCY_BY_COUNTRY_JSON,
         "cap_json": CAPITAL_BY_COUNTRY_JSON, "form": form_values, "countries": COUNTRIES},
    )
