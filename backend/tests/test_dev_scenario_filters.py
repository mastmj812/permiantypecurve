"""Development-scenario sync + filters -- DB-free unit tests.

engineering_db ``curated.dev_scenario`` (sql/50) classifies each producing
horizontal at first production (sandwich > topfill > underfill >
codev_stack > standalone; 660-ft offset gate, 1,000-ft band, shielding).
The app syncs it verbatim onto ``wells.scenario_*`` and filters on it in
TWO implementations -- ``FilterSpec.to_sqlalchemy_clauses`` (select /
summary / facets) and ``tiles._build_filter_sql`` (map). These tests pin
the passthrough and that both implementations say the same thing.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import ClauseElement

from app.ingest.wells import header_to_upsert_values
from app.warehouse_client.base import WellHeader
from app.warehouse_client.wells import (
    _FETCH_ONE_SQL,
    _HEADER_COLUMNS_SQL,
    _row_to_dto,
    fetch_well_headers,
)
from app.wells_api.filters import (
    PARENT_OFFSET_GATE_FT,
    SCENARIO_CLASS_VALUES,
    FilterSpec,
    filter_spec_dict,
    parse_filter_query,
)
from app.wells_api.selection import FilterSpecBody
from app.wells_api.tiles import _build_filter_sql, _lines_sql, _points_sql

from .test_water_provenance import _full_row

_PG = postgresql.dialect()  # type: ignore[no-untyped-call]  # SA dialect ctor is untyped

_CTX = {
    "LSSH": {
        "n": 3,
        "n_codev": 0,
        "n_parent": 2,
        "n_child": 1,
        "parent_min_offset_ft": 210,
        "parent_nearest_dtvd_ft": -520,
        "parent_min_age_days": 800,
        "parent_max_age_days": 2100,
        "codev_nearest_dtvd_ft": None,
    }
}


# ---------------------------------------------------------------- sync


def _compile(clause: ClauseElement) -> str:
    return str(clause.compile(dialect=_PG))


def test_offset_gate_is_the_warehouse_constant() -> None:
    # CROSS-REPO CONTRACT with engineering_db sql/50 + find_analogs.py.
    assert PARENT_OFFSET_GATE_FT == 660.0
    assert SCENARIO_CLASS_VALUES == (
        "sandwich",
        "topfill",
        "underfill",
        "codev_stack",
        "standalone",
    )


def test_row_to_dto_maps_scenario_block() -> None:
    from decimal import Decimal

    dto = _row_to_dto(
        _full_row(
            scenario_bench="WCA_1",
            scenario_class="underfill",
            parent_benches_above=["LSSH"],
            parent_benches_below=[],
            nearest_parent_above_dtvd_ft=Decimal("-520"),
            nearest_parent_offset_ft=Decimal("210"),
            shielded_below=False,
            shielded_above=False,
            youngest_parent_age_days=800,
            oldest_parent_age_days=2100,
            has_same_bench_parent=True,
            codev_benches_other=["WCB_2"],
            child_benches_other=[],
            child_censored=False,
            scenario_bench_context=_CTX,
        )
    )
    assert dto.scenario_bench == "WCA_1"
    assert dto.scenario_class == "underfill"
    assert dto.parent_benches_above == ("LSSH",)
    assert dto.parent_benches_below == ()
    # numeric -> float widening
    assert dto.nearest_parent_above_dtvd_ft == -520.0
    assert isinstance(dto.nearest_parent_offset_ft, float)
    assert dto.youngest_parent_age_days == 800
    assert dto.codev_benches_other == ("WCB_2",)
    assert dto.scenario_bench_context == _CTX


def test_row_to_dto_scenario_nullable() -> None:
    # LEFT JOIN miss (not in dev_scenario): NULLs, never a fabricated class.
    dto = _row_to_dto(_full_row())
    assert dto.scenario_class is None
    assert dto.parent_benches_above is None
    assert dto.scenario_bench_context is None


def test_upsert_values_pass_scenario_through() -> None:
    h = WellHeader(
        api10="4200000001",
        scenario_bench="WCA_1",
        scenario_class="topfill",
        parent_benches_below=("WCC",),
        codev_benches_other=(),
        scenario_bench_context=_CTX,
    )
    v = header_to_upsert_values(h, datetime.now(UTC))
    assert v["scenario_bench"] == "WCA_1"
    assert v["scenario_class"] == "topfill"
    assert v["parent_benches_below"] == ["WCC"]  # tuple -> list for ARRAY
    assert v["codev_benches_other"] == []
    assert v["parent_benches_above"] is None
    assert v["scenario_bench_context"] == _CTX


def test_bench_context_none_stores_sql_null_not_json_null() -> None:
    # JSONB default stores Python None as JSON 'null'; jsonb_each() on that
    # raises and would break every parent-bench tile. Found on the first
    # dev sync (5,535 wells absent from dev_scenario).
    from app.db.models import Well

    col_type = Well.__table__.c.scenario_bench_context.type
    assert getattr(col_type, "none_as_null", False) is True


def test_both_fetch_paths_join_dev_scenario() -> None:
    single = str(_FETCH_ONE_SQL)
    assert "LEFT JOIN curated.dev_scenario ds ON ds.api10 = we.api10" in single
    assert "ds.bench                             AS scenario_bench" in single
    assert "ds.bench_context                     AS scenario_bench_context" in single
    assert "ds.scenario_class" in _HEADER_COLUMNS_SQL
    assert "curated.dev_scenario ds" in inspect.getsource(fetch_well_headers)


# ---------------------------------------------------------------- filters


def test_default_spec_emits_no_scenario_sql() -> None:
    sql, params = _build_filter_sql(FilterSpec())
    assert "scenario" not in sql and "parent" not in sql
    assert not any(k.startswith(("scenario", "parent")) for k in params)
    assert not any("scenario" in _compile(c) for c in FilterSpec().to_sqlalchemy_clauses())


def test_scenario_classes_no_data_both_sides() -> None:
    spec = FilterSpec(scenario_classes=("topfill", "no_data"))
    sql, params = _build_filter_sql(spec)
    assert "w.scenario_class = ANY((:scenario_classes)::TEXT[])" in sql
    assert "w.scenario_class IS NULL" in sql
    assert params["scenario_classes"] == ["topfill"]
    orm = " ".join(_compile(c) for c in spec.to_sqlalchemy_clauses())
    assert "wells.scenario_class IN" in orm and "wells.scenario_class IS NULL" in orm


def test_scenario_benches_key_on_tvd_corrected_bench() -> None:
    spec = FilterSpec(scenario_benches=("WCA_1",))
    sql, _ = _build_filter_sql(spec)
    assert "w.scenario_bench = ANY" in sql and "formation_blueox" not in sql
    orm = " ".join(_compile(c) for c in spec.to_sqlalchemy_clauses())
    assert "wells.scenario_bench IN" in orm


# The bench-pair clause: after normalizing binds and aliases, the ORM
# EXISTS and the tile-SQL EXISTS must carry the IDENTICAL predicate set.


def _orm_pair_predicates(spec: FilterSpec) -> set[str]:
    from sqlalchemy import select

    from app.db.models import Well

    q = select(Well.api10).where(*spec.to_sqlalchemy_clauses())
    s = " ".join(str(q.compile(dialect=_PG, compile_kwargs={"literal_binds": True})).split())
    # correlated: the subquery FROM is the jsonb_each alone, no bare `wells`
    assert "FROM jsonb_each(wells.scenario_bench_context) AS j WHERE" in s
    inner = s.split("FROM jsonb_each(wells.scenario_bench_context) AS j WHERE ", 1)[1]
    inner = inner.rsplit("))", 1)[0]
    return set(inner.split(" AND "))


def _tile_pair_predicates(spec: FilterSpec) -> set[str]:
    sql, params = _build_filter_sql(spec)
    inner = sql.split("AS j(key, value) WHERE ", 1)[1].rsplit(")", 1)[0]
    inner = inner.replace(
        "j.key = ANY((:parent_benches)::TEXT[])",
        "j.key IN (" + ", ".join(f"'{b}'" for b in spec.parent_benches) + ")",
    )
    for k, v in params.items():
        if k != "parent_benches":
            inner = inner.replace(f":{k}", repr(v))
    return set(inner.replace("w.scenario_bench", "wells.scenario_bench").split(" AND "))


@pytest.mark.parametrize(
    "kw",
    [
        {},
        {"parent_side": "above"},
        {"parent_side": "below", "parent_dtvd_max_ft": 700.0},
        {"parent_age_min_days": 730, "parent_age_max_days": 3000},
        {"parent_side": "above", "parent_dtvd_max_ft": 900.0, "parent_age_min_days": 180},
    ],
)
def test_parent_bench_pair_orm_and_tile_predicates_identical(kw: dict[str, object]) -> None:
    spec = FilterSpec(parent_benches=("LSSH", "JM"), **kw)  # type: ignore[arg-type]
    orm, tile = _orm_pair_predicates(spec), _tile_pair_predicates(spec)
    assert orm == tile
    # the offset gate is always there; no vertical window unless asked
    assert "CAST(j.value ->> 'parent_min_offset_ft' AS NUMERIC) <= 660.0" in orm
    if "parent_dtvd_max_ft" not in kw:
        assert not any(p.startswith("abs(") for p in orm)


def test_parent_side_and_cap_are_inert_without_parent_benches() -> None:
    base_sql = _build_filter_sql(FilterSpec())
    inert = FilterSpec(parent_side="above", parent_dtvd_max_ft=500.0)
    assert _build_filter_sql(inert) == base_sql
    assert len(inert.to_sqlalchemy_clauses()) == len(FilterSpec().to_sqlalchemy_clauses())


def test_parent_age_scopes_to_pair_or_class_both_sides() -> None:
    pair = FilterSpec(parent_benches=("LSSH",), parent_age_min_days=730)
    sql, _ = _build_filter_sql(pair)
    assert "'parent_min_age_days' AS INTEGER) >= :parent_age_min_days" in sql
    assert "w.youngest_parent_age_days" not in sql
    orm = " ".join(_compile(c) for c in pair.to_sqlalchemy_clauses())
    assert "youngest_parent_age_days" not in orm

    cls = FilterSpec(parent_age_min_days=730, parent_age_max_days=3000)
    sql, _ = _build_filter_sql(cls)
    assert "w.youngest_parent_age_days >= :parent_age_min_days" in sql
    assert "w.oldest_parent_age_days <= :parent_age_max_days" in sql
    orm = " ".join(_compile(c) for c in cls.to_sqlalchemy_clauses())
    assert "wells.youngest_parent_age_days >=" in orm
    assert "wells.oldest_parent_age_days <=" in orm


def test_parse_filter_query_scenario_params_tolerant() -> None:
    spec = parse_filter_query(
        scenario_classes="topfill,bogus,no_data",
        scenario_benches="WCA_1",
        parent_benches="LSSH,JM",
        parent_side="sideways",
        parent_dtvd_max_ft=800.0,
        parent_age_min_days=365,
    )
    assert spec.scenario_classes == ("topfill", "no_data")  # unknown dropped
    assert spec.parent_benches == ("LSSH", "JM")
    assert spec.parent_side == "any"  # unknown side -> any
    assert spec.parent_dtvd_max_ft == 800.0
    d = filter_spec_dict(spec)
    assert d["scenario_classes"] == ["topfill", "no_data"]
    assert d["parent_side"] == "any"


def test_selection_body_round_trips_to_same_spec() -> None:
    # The lasso/box path must build the SAME spec the map tiles use.
    q = parse_filter_query(
        scenario_classes="underfill",
        scenario_benches="WCB_2",
        parent_benches="WCA_1",
        parent_side="above",
        parent_dtvd_max_ft=900.0,
        parent_age_min_days=180,
        parent_age_max_days=4000,
    )
    body = FilterSpecBody(
        scenario_classes=["underfill", "bogus"],
        scenario_benches=["WCB_2"],
        parent_benches=["WCA_1"],
        parent_side="above",
        parent_dtvd_max_ft=900.0,
        parent_age_min_days=180,
        parent_age_max_days=4000,
    ).to_spec()
    for f in (
        "scenario_classes",
        "scenario_benches",
        "parent_benches",
        "parent_side",
        "parent_dtvd_max_ft",
        "parent_age_min_days",
        "parent_age_max_days",
    ):
        assert getattr(body, f) == getattr(q, f), f


def test_tiles_carry_scenario_class_property() -> None:
    # MVT property for the map's color-by-scenario (MVT omits NULLs --
    # the style must coalesce).
    for sql in (_points_sql(), _lines_sql()):
        assert "w.scenario_class," in sql
