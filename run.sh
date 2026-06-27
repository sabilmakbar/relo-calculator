#!/bin/bash
# Run via `uv run` so the project's .venv is used regardless of any active
# conda/virtualenv in the shell (avoids cross-env version skew, e.g. uvicorn
# 'ssl_context_factory' errors from a mismatched interpreter).
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
