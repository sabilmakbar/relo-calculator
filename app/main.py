from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .scraping import load_city_differences
from .model import compute_model

app = FastAPI()

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


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
    net_new: float | None = Form(None),
    increment_pct: float | None = Form(None),
):
    error = None
    result = None

    try:
        # Derive new salary if only increment is given
        if net_new is None:
            if increment_pct is None:
                raise ValueError("Provide net_new or increment_pct")
            net_new = net_home * (1 + increment_pct / 100)

        diffs = await load_city_differences(country1, city1, country2, city2)

        model = compute_model(
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
