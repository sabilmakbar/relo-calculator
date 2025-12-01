from pathlib import Path
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .data_sources import get_percentage_diff
from .model import calculate_stats

app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


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

    net_new = float(net_new) if net_new else None
    increment_pct = float(increment_pct) if increment_pct else None
    try:
        # Derive new salary if only increment is given
        if net_new is None:
            if increment_pct is None:
                raise ValueError("Provide net_new or increment_pct")
            net_new = net_home * (1 + increment_pct / 100)

        country1, country2 = country1.capitalize(), country2.capitalize()
        city1, city2 = city1.capitalize(), city2.capitalize()

        diffs = await get_percentage_diff(country1, city1, country2, city2, )

        model = calculate_stats(
            net_home,
            net_new,
            diffs["col_excl_rent"],
            diffs["rent"],
        )

        result = {"cities": diffs, "model": model}

    except Exception as e:
        error = f"Error: {e}"

    return templates.TemplateResponse(
        "index.html",
        {"request": request, "result": result, "error": error},
    )
