"""The compute/save request default alignment must be the convention of
record: `peak_ramp`.

Reached only when a programmatic caller omits `alignment_method` (the UI
always sends it explicitly). Before this was pinned, the default was the
legacy `first_prod_month`, which the audit found biased EUR ~7% high and
which `/save` then persisted as the curve's alignment. peak_ramp still
carries the ramp months economics needs, so there's no reason to default
to the smeared alignment.
"""

from __future__ import annotations

from app.api.type_curves import ComputeRequest, SaveRequest
from app.cli.tc_di_diagnostic import build_parser


def test_compute_request_defaults_to_peak_ramp() -> None:
    req = ComputeRequest(api10s=["4212345678"])
    assert req.alignment_method == "peak_ramp"


def test_save_request_defaults_to_peak_ramp() -> None:
    req = SaveRequest(name="tc", included_api10s=["4212345678"])
    assert req.alignment_method == "peak_ramp"


def test_explicit_alignment_is_still_honored() -> None:
    # The default must not clobber an explicit legacy choice.
    req = ComputeRequest(api10s=["4212345678"], alignment_method="first_prod_month")
    assert req.alignment_method == "first_prod_month"


def test_cli_diagnostic_defaults_to_peak_ramp() -> None:
    # The unsaved-cohort diagnostic should reproduce what the app
    # aggregates by default, not the legacy first_prod_month.
    args = build_parser().parse_args(["--api10s", "4212345678"])
    assert args.alignment == "peak_ramp"


def test_cli_diagnostic_explicit_alignment_honored() -> None:
    args = build_parser().parse_args(
        ["--api10s", "4212345678", "--alignment", "first_prod_month"]
    )
    assert args.alignment == "first_prod_month"
