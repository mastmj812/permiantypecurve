"""Daily-rate computation for monthly production records.

Two rate columns are persisted per stream:

  rate_prodday_*  =  monthly_volume / producing_days
  rate_calday_*   =  monthly_volume / calendar_days_in_month

`rate_prodday_*` is DIAGNOSTIC ONLY (per-well detail toggle). All
forecasting, peak detection, and type-curve aggregation use `rate_calday_*`,
because Enverus' `producing_days` is unreliable past month 1.

Month-1 exception (REQUIRED — corrupts peak-month detection if skipped):

    If the production month equals the first-prod month AND
    producing_days is reported AND producing_days < calendar days in month
    THEN use producing_days as the calday denominator anyway.

The first-prod month is typically partial (e.g. spud + first sale on day
22 of the month). Dividing the partial volume by 30/31 days would understate
peak rate by ~3× and shift the "true" peak to month 2, corrupting both fits
and type curve alignment.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RateInputs:
    prod_date: date
    first_prod_date: date | None
    # Float — Enverus reports fractional days for partial first months.
    producing_days: float | None
    oil_bbl: float | None
    gas_mcf: float | None
    water_bbl: float | None


@dataclass(frozen=True)
class RateOutputs:
    rate_calday_bopd: float | None
    rate_calday_mcfd: float | None
    rate_calday_bwpd: float | None
    rate_prodday_bopd: float | None
    rate_prodday_mcfd: float | None
    rate_prodday_bwpd: float | None


def calendar_days_in(month_date: date) -> int:
    return monthrange(month_date.year, month_date.month)[1]


def _is_first_prod_partial_month(
    prod_date: date, first_prod_date: date | None, producing_days: float | None
) -> bool:
    if first_prod_date is None or producing_days is None:
        return False
    if prod_date.year != first_prod_date.year or prod_date.month != first_prod_date.month:
        return False
    return 0 < producing_days < calendar_days_in(prod_date)


def _safe_div(volume: float | None, denom: float | None) -> float | None:
    if volume is None or denom is None or denom <= 0:
        return None
    return volume / denom


def compute_rates(inputs: RateInputs) -> RateOutputs:
    cal_days = calendar_days_in(inputs.prod_date)
    use_pd_for_calday = _is_first_prod_partial_month(
        inputs.prod_date, inputs.first_prod_date, inputs.producing_days
    )
    calday_denom: float = (
        inputs.producing_days if use_pd_for_calday else cal_days  # type: ignore[assignment]
    )
    # When use_pd_for_calday is True, _is_first_prod_partial_month already
    # guarantees producing_days is a positive int.

    pd_denom = inputs.producing_days if inputs.producing_days and inputs.producing_days > 0 else None

    return RateOutputs(
        rate_calday_bopd=_safe_div(inputs.oil_bbl, calday_denom),
        rate_calday_mcfd=_safe_div(inputs.gas_mcf, calday_denom),
        rate_calday_bwpd=_safe_div(inputs.water_bbl, calday_denom),
        rate_prodday_bopd=_safe_div(inputs.oil_bbl, pd_denom),
        rate_prodday_mcfd=_safe_div(inputs.gas_mcf, pd_denom),
        rate_prodday_bwpd=_safe_div(inputs.water_bbl, pd_denom),
    )
