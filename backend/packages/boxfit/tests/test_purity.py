"""boxfit must import with no anduin / DB / web stack — it is the engine the
engineering_db BOX batch installs on its own."""

from __future__ import annotations

import subprocess
import sys

MODULES = [
    "boxfit.types",
    "boxfit.models",
    "boxfit.metrics",
    "boxfit.cumulative",
    "boxfit.eur",
    "boxfit.peak_detection",
    "boxfit.ramp_arps",
    "boxfit.ratio",
    "boxfit.fit",
    "boxfit.b_prior",
    "boxfit.well",
    "boxfit.tc.align",
    "boxfit.tc.aggregate",
    "boxfit.tc.fit_p50",
    "boxfit.tc.ratio_mode",
]
FORBIDDEN = ("app", "sqlalchemy", "fastapi", "pydantic", "psycopg", "geoalchemy2")


def test_imports_pull_no_app_or_db_stack() -> None:
    # Fresh interpreter from "/": the pytest process already has anduin loaded,
    # and the backend dir on sys.path would make `app` importable.
    code = (
        "import sys\n"
        + "".join(f"import {m}\n" for m in MODULES)
        + f"bad = sorted(m for m in sys.modules if m.split('.')[0] in {FORBIDDEN!r})\n"
        + "print(','.join(bad))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd="/"
    )
    assert out.stdout.strip() == ""


def test_b_priors_ship_as_package_data() -> None:
    from boxfit.b_prior import PRIORS_PATH

    assert PRIORS_PATH.exists()
    assert PRIORS_PATH.parent.parent.name == "boxfit"
