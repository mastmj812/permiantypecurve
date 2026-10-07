"""Dossier zone summary: the per-zone math (risked TC vs Novi, gap flag,
Di nominal + effective, cohort QC) and the summary slide it feeds.
DB-free: a stub TypeCurve and stub Forecast rows."""

from __future__ import annotations

import io
import math
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from pptx import Presentation
from pptx.shapes.graphfrm import GraphicFrame

from app.db.models import TypeCurve
from app.exports.dossier import SUMMARY_ROWS_PER_SLIDE, build_deal_dossier_pptx
from app.exports.dossier_summary import (
    GAP_FLAG_RATIO,
    SUMMARY_HEADERS,
    CohortQC,
    ZoneStick,
    build_zone_summary,
    cohort_qc,
    novi_eur_per_1000ft,
    one_yr_effective,
    stream_summary,
    summary_cells,
)

_DI_HI = {"oil": 4.0, "gas": 4.0}


def _tc(
    oil_eur: float = 60_000.0, gas_eur: float = 300_000.0, risk: dict[str, Any] | None = None
) -> TypeCurve:
    tc = TypeCurve()
    tc.id = uuid.uuid4()
    tc.name = "alchemist_wcb2"
    tc.included_api10s = ["4230100001", "4230100002", "4230100003"]
    tc.risk_multipliers = risk or {}
    tc.forecast_overrides = {}
    tc.series = {
        "streams": {
            "oil": {"fitted": {"qi": 30.0, "Di": 2.5, "b": 1.0, "eur_per_unit": oil_eur}},
            "gas": {"fitted": {"qi": 90.0, "Di": 1.8, "b": 1.1, "eur_per_unit": gas_eur}},
        }
    }
    return tc


def _fc(qi: float = 500.0, di: float = 2.0, b: float = 1.0, peak: float = 500.0) -> Any:
    return SimpleNamespace(qi=qi, di_initial=di, b=b, peak_rate=peak)


def _stick(cat: str = "PUD", ll: float = 10_000.0, scen: str = "d/s1") -> ZoneStick:
    return ZoneStick(
        well_name="gen-0",
        formation="WCB_2",
        category=cat,
        scenario_ref=scen,
        completed_lateral_ft=ll,
        target_tvd_ft=9_800.0,
    )


def test_effective_matches_house_formula() -> None:
    # nominal 2.5/yr, b=1 -> 1 - 1/(1+2.5) = 71.4% effective
    assert one_yr_effective(2.5, 1.0) == pytest.approx(2.5 / 3.5)
    assert one_yr_effective(1.0, 0.0) == pytest.approx(1 - math.exp(-1.0))
    assert one_yr_effective(None, 1.0) is None


def test_stream_summary_risked_and_gap_flag() -> None:
    s = stream_summary(_tc(risk={"oil": 0.8}), "oil", novi_eur=30_000.0)
    assert s.eur_per_1000ft == pytest.approx(48_000.0)  # 60k x 0.8 — risked as delivered
    assert s.qi_per_1000ft == pytest.approx(24.0)
    assert s.risk_mult == 0.8
    assert s.tc_vs_novi == pytest.approx(0.6)  # 48k / 30k - 1
    assert s.gap_flag  # 1.6x > 1.5x
    ok = stream_summary(_tc(), "oil", novi_eur=50_000.0)
    assert ok.tc_vs_novi == pytest.approx(0.2) and not ok.gap_flag
    low = stream_summary(_tc(), "oil", novi_eur=60_000.0 * GAP_FLAG_RATIO + 1)
    assert low.gap_flag  # flag fires both ways
    none = stream_summary(_tc(), "oil", novi_eur=None)
    assert none.tc_vs_novi is None and not none.gap_flag


def test_novi_eur_is_the_summed_median_series() -> None:
    assert novi_eur_per_1000ft([10.0] * 600) == 6_000.0
    assert novi_eur_per_1000ft(()) is None


def test_cohort_qc_override_wins_and_bounds_per_stream() -> None:
    tc = _tc()
    # well 1: global oil fit Di pinned at the 4.0 cap; well 2: TC override
    # (clean) replaces a pinned global; well 3: no oil forecast at all.
    tc.forecast_overrides = {
        "4230100002": {"oil": {"qi": 400.0, "di_initial": 2.0, "b": 1.0, "peak_rate": 400.0}}
    }
    forecasts = {
        "oil": {"4230100001": _fc(di=3.99), "4230100002": _fc(di=3.99)},
        "gas": {a: _fc() for a in tc.included_api10s},
    }
    qc = cohort_qc(tc, forecasts, _DI_HI)  # type: ignore[arg-type]
    assert qc.n_wells == 3 and qc.n_overridden == 1
    assert qc.at_bound == {"oil": 1, "gas": 0}
    assert qc.di_at_bound == {"oil": 1, "gas": 0} and qc.b_at_bound == {"oil": 0, "gas": 0}
    assert qc.missing == {"oil": 1, "gas": 0}


def test_zone_summary_counts_and_cells() -> None:
    tc = _tc(risk={"oil": 0.8, "gas": 0.8})
    z = build_zone_summary(
        zone_name="WCB_2",
        reserve_category="PUD",
        benches=["WCB_2"],
        tc=tc,
        sticks=[
            _stick("PUD", 9_800.0),
            _stick("UPSIDE", 10_200.0, "d/s2"),
            _stick("PUD", 10_000.0),
        ],
        novi_volumes={"oil": [50.0] * 600, "gas": [500.0] * 600},
        novi_n_sticks=7,
        novi_low_n=False,
        novi_stale=False,
        qc=CohortQC(
            n_wells=3,
            n_overridden=0,
            at_bound={"oil": 2, "gas": 0},
            di_at_bound={"oil": 1, "gas": 0},
            b_at_bound={"oil": 2, "gas": 0},
            missing={"oil": 0, "gas": 0},
        ),
    )
    assert (z.n_sticks, z.n_pud, z.n_upside, z.n_scenarios) == (3, 2, 1, 2)
    assert z.planned_lateral_ft_median == 10_000.0
    assert (
        z.curve_name.startswith("alchemist_wcb2") and z.curve_name != "alchemist_wcb2"
    )  # risked suffix
    cells = summary_cells(z)
    assert len(cells) == len(SUMMARY_HEADERS)
    row = dict(zip(SUMMARY_HEADERS, cells, strict=True))
    assert row["Sticks (PUD / UPSIDE)"] == "3 (2 / 1)"
    assert row["Oil EUR /1,000 ft"] == "48,000" and row["Novi oil"] == "30,000"
    assert row["Oil TC vs Novi"] == "+60% ⚑"
    assert row["Oil Di nom /yr (1-yr eff)"] == "2.50 (71%)"  # nominal with effective beside it
    assert row["Risk"] == "×0.8"
    assert "oil pinned: Di 1 · b 2" in row["QC"]
    assert any("TC 1.60x Novi" in f for f in z.flags)
    assert any("1 cohort oil fits with Di at a bound" in f for f in z.flags)


def _summary(n: int) -> list[Any]:
    tc = _tc()
    return [
        build_zone_summary(
            zone_name=f"Z{i}",
            reserve_category="PUD",
            benches=["WCB_2"],
            tc=tc,
            sticks=[_stick()],
            novi_volumes=None,
            novi_n_sticks=0,
            novi_low_n=False,
            novi_stale=False,
            qc=CohortQC(n_wells=3, n_overridden=0),
            novi_error="Novi comparison unavailable: down",
        )
        for i in range(n)
    ]


def test_summary_slide_leads_the_deck() -> None:
    content = build_deal_dossier_pptx(SimpleNamespace(), [], [], summary=_summary(2))  # type: ignore[arg-type]
    pres = Presentation(io.BytesIO(content))
    assert len(pres.slides) == 1
    tables = [s for s in pres.slides[0].shapes if isinstance(s, GraphicFrame) and s.has_table]
    assert len(tables) == 1
    t = tables[0].table
    assert len(t.rows) == 3 and len(t.columns) == len(SUMMARY_HEADERS)
    assert t.cell(0, 0).text == "Zone" and t.cell(1, 0).text == "Z0"
    assert "Novi unavailable" in t.cell(1, len(SUMMARY_HEADERS) - 1).text


def test_summary_paginates() -> None:
    content = build_deal_dossier_pptx(
        SimpleNamespace(),
        [],
        [],
        summary=_summary(SUMMARY_ROWS_PER_SLIDE + 1),  # type: ignore[arg-type]
    )
    assert len(Presentation(io.BytesIO(content)).slides) == 2
