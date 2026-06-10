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

COPY pyproject.toml constraints.txt ./
# Stub package so `pip install -e` can resolve the project layout without the
# real source. The COPY below overwrites it with the actual app/__init__.py.
# This split keeps the heavy deps layer cached across source changes.
RUN mkdir -p app && printf '__version__ = "0.0.0"\n' > app/__init__.py
# constraints.txt pins the exact resolved versions (pip freeze of the
# known-good image). Without it every rebuild floats on the `>=` ranges
# in pyproject — a silent scipy/numpy bump can shift curve_fit results
# (qi/Di/b -> EUR) with nothing to catch it. Regenerate deliberately:
#   docker exec permian-backend sh -c "pip freeze --exclude-editable" > backend/constraints.txt
# ...then run the test suite (the real-well baselines are the tripwire).
RUN pip install --upgrade pip && pip install -e ".[dev]" -c constraints.txt

COPY app ./app
COPY tests ./tests
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts

EXPOSE 8000
CMD ["bash", "scripts/entrypoint.sh"]
