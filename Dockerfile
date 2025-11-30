# ---------------------------
# 1. Build stage
# ---------------------------
FROM python:3.11-slim as builder

ENV UV_SYSTEM_PYTHON=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=on
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install curl & build essentials (needed for uv)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv (Astral)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies into /app/.venv
RUN ~/.local/bin/uv sync --frozen

# Copy source code
COPY app ./app


# ---------------------------
# 2. Runtime stage (lean)
# ---------------------------
FROM python:3.11-slim as runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv ./.venv

# Copy application code
COPY app ./app

# Use venv python automatically
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
