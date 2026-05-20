"""Bulk-clear `manual_override` and `locked` on stored per-well forecasts.

    # Preview what would be touched, no DB writes:
    python -m app.cli.reset_forecast_flags --dry-run

    # Clear flags on every forecast in the DB:
    python -m app.cli.reset_forecast_flags

    # Scope to a specific api14 list (CSV or repeated --api14):
    python -m app.cli.reset_forecast_flags \\
        --api14s 42301343020000,42301372360000,42301366170000

    # Same scope but also wipe the override params back to whatever the
    # current auto-fitter would produce. Re-runs the orchestrator on
    # those wells in process — slower; expect a few seconds per well.
    python -m app.cli.reset_forecast_flags --api14s ... --refit

The two flags exist for two distinct reasons:
  * `manual_override = True` is set whenever the detail-modal Save
    Override path patches the params (forecasts.py::patch_forecast).
    The Review tab badges these wells "edited".
  * `locked = True` is set by the lock-toggle button in the detail
    modal. The batch orchestrator skips locked wells on re-fit, so
    flipping this is what lets a bulk re-fit actually touch the row.

Clearing the flags alone DOES NOT change the persisted Arps params —
the row's qi / Di / b / Df stay at whatever the user last edited them
to. Pass --refit to also rerun the fitter and overwrite the params
with a fresh auto-fit. The upsert path explicitly excludes the two
flag columns from its SET clause, so the order of operations is:
clear flags first, then re-fit, otherwise refit silently skips
anything still locked.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select, update

from app.core.logging import configure_logging, get_logger
from app.db.models import Forecast
from app.db.session import SessionLocal


def _parse_api14s(raw: list[str] | None) -> list[str] | None:
    if not raw:
        return None
    out: list[str] = []
    for chunk in raw:
        for piece in chunk.split(","):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clear manual_override + locked on stored forecasts"
    )
    parser.add_argument(
        "--api14s",
        action="append",
        default=None,
        help="Comma-separated api14 list (or repeated --api14s) to scope "
             "the reset. Omit to hit every forecast row.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be touched without writing.",
    )
    parser.add_argument(
        "--refit",
        action="store_true",
        help="After clearing flags, re-run the auto-fitter on the same "
             "wells via app.forecasting.orchestrator.run_forecast_batch. "
             "Required if you want the persisted params to also revert.",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    configure_logging(args.log_level)
    log = get_logger("cli.reset_forecast_flags")

    api14s = _parse_api14s(args.api14s)

    with SessionLocal() as session:
        # Count what would be touched. Two flavors so the operator can
        # see whether they're clearing edits, locks, or both.
        q_edited = select(Forecast).where(Forecast.manual_override.is_(True))
        q_locked = select(Forecast).where(Forecast.locked.is_(True))
        if api14s is not None:
            q_edited = q_edited.where(Forecast.api14.in_(api14s))
            q_locked = q_locked.where(Forecast.api14.in_(api14s))
        n_edited = len(session.execute(q_edited).scalars().all())
        n_locked = len(session.execute(q_locked).scalars().all())

        scope_desc = (
            f"{len(api14s)} api14s" if api14s is not None else "all wells"
        )
        print(
            f"Scope: {scope_desc}. "
            f"Would clear manual_override on {n_edited} forecast(s), "
            f"locked on {n_locked} forecast(s)."
        )
        if args.dry_run:
            print("--dry-run set; no DB writes.")
            return 0

        stmt = (
            update(Forecast)
            .values(manual_override=False, locked=False)
            .where((Forecast.manual_override.is_(True)) | (Forecast.locked.is_(True)))
        )
        if api14s is not None:
            stmt = stmt.where(Forecast.api14.in_(api14s))
        result = session.execute(stmt)
        session.commit()
        log.info(
            "flags_cleared",
            rows_updated=result.rowcount,
            scope=scope_desc,
        )
        print(f"Cleared flags on {result.rowcount} forecast row(s).")

    if args.refit:
        # Late import: pulls numpy / scipy / the loader. Don't want this
        # on the import path for the flag-only case.
        from app.forecasting.orchestrator import forecast_wells
        from app.forecasting.types import ForecastConfig

        if api14s is None:
            # The orchestrator wants an explicit api14 list. Pull every
            # api14 with a forecast row so "refit everything" still works.
            with SessionLocal() as session:
                api14s = [
                    r[0]
                    for r in session.execute(select(Forecast.api14).distinct()).all()
                ]

        print(f"Re-fitting {len(api14s)} well(s)…")
        cfg = ForecastConfig()
        with SessionLocal() as session:
            results = forecast_wells(session, api14s, config=cfg)
        n_ok = sum(
            1
            for streams in results.values()
            for r in streams.values()
            if r is not None
        )
        n_failed = sum(
            1
            for streams in results.values()
            for r in streams.values()
            if r is None
        )
        log.info(
            "refit_done",
            wells=len(api14s),
            streams_ok=n_ok,
            streams_failed=n_failed,
        )
        print(
            f"Refit: {len(api14s)} wells, {n_ok} stream-fits succeeded, "
            f"{n_failed} skipped/failed."
        )

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
