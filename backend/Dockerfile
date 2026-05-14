FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps for psycopg + scipy/numpy wheels (libgomp, libpq for runtime).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# Stub package so `pip install -e` can resolve the project layout without the
# real source. The COPY below overwrites it with the actual app/__init__.py.
# This split keeps the heavy deps layer cached across source changes.
RUN mkdir -p app && printf '__version__ = "0.0.0"\n' > app/__init__.py
RUN pip install --upgrade pip && pip install -e ".[dev]"

COPY app ./app
COPY tests ./tests
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts

EXPOSE 8000
CMD ["bash", "scripts/entrypoint.sh"]
