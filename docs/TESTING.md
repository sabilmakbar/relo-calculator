# Testing

The suite uses `pytest` with `pytest-asyncio`; network calls (Numbeo, Yahoo, Frankfurter, currency-api) are mocked via `respx` and monkeypatching — no live requests.

```bash
uv sync --group dev          # install test + audit dependencies
uv run pytest                # run the suite
uv run pytest --cov          # run with a coverage report
uv run pytest --cov --cov-report=html   # HTML report in htmlcov/
uv run pip-audit             # scan dependencies for known CVEs
```

## Layers & coverage

Latest run — **134 tests, 93% line coverage**, spanning four layers:

| Layer | File | Focus |
|---|---|---|
| Unit | `test_model/fx/currencies/data_sources/tax.py` | Model math, EMA blend, lookups, parsing, tax rates |
| Integration | `test_integration.py` | Full request → model → render pipeline |
| Regression | `test_regression.py` | Pins previously-fixed bugs |
| Security | `test_security.py` | Validation, XSS escaping, SSRF resistance |
| Endpoint | `test_app.py`, `test_tax.py` | Routes, validation, degradation |

| Module | Coverage |
|---|---|
| `app/currencies.py` | 100% |
| `app/fx.py` | 96% |
| `app/model.py` | 96% |
| `app/data_sources.py` | 96% |
| `app/main.py` | ~86% (uncovered: rare HTTP error branches) |
| **Total** | **93%** |

## Security

See [SECURITY_TEST.md](../SECURITY_TEST.md) for the security review — findings rated by CVSS band and mapped to the OWASP Top 10. The dependency audit is clean after upgrading `python-multipart` / `starlette` / `idna` / `python-dotenv` (14 CVEs resolved); `pip-audit` is wired into the dev group to keep it that way.
