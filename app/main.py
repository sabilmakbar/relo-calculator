import logging
import re
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .data_sources import get_percentage_diff
from .model import calculate_stats

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

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


@app.get("/", response_class=HTMLResponse)
async def form(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "result": None, "error": None},
    )


@app.post("/compare", response_class=HTMLResponse)
async def compare(
    request: Request,
    country1: str = Form(...),
    city1: str = Form(...),
    country2: str = Form(...),
    city2: str = Form(...),
    net_home: float = Form(...),
    net_new: (str | None) = Form(None),
    increment_pct: (str | None) = Form(None),
):
    error = None
    result = None

    try:
        country1 = _validate_place("Country 1", country1).capitalize()
        city1 = _validate_place("City 1", city1).capitalize()
        country2 = _validate_place("Country 2", country2).capitalize()
        city2 = _validate_place("City 2", city2).capitalize()

        if net_home <= 0:
            raise ValueError("Current net salary must be a positive number.")

        net_new_f = float(net_new) if net_new and net_new.strip() else None
        increment_pct_f = float(increment_pct) if increment_pct and increment_pct.strip() else None

        if net_new_f is None:
            if increment_pct_f is None:
                raise ValueError("Provide either a new net salary or an expected increase %.")
            net_new_f = net_home * (1 + increment_pct_f / 100)

        if net_new_f <= 0:
            raise ValueError("New net salary must be a positive number.")

        diffs = await get_percentage_diff(country1, city1, country2, city2)

        model = calculate_stats(
            net_home,
            net_new_f,
            diffs["col_excl_rent"],
            diffs["rent"],
        )

        result = {"cities": diffs, "model": model}

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
        "index.html",
        {"request": request, "result": result, "error": error},
    )
