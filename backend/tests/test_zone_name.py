"""Blue Ox zone-name grammar (exports/zone_name.py) + the API-layer
resolve/validate helpers. The golden tables are mirrored in
frontend/src/api/zoneName.test.ts."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.api.deals import BlueOxZoneSpec, _resolve_zone_names, _zone_name_errors
from app.exports.zone_name import (
    DEFAULT_CASES,
    GOLDEN_CASES,
    default_zone_name,
    zone_name_error,
)


@pytest.mark.parametrize(("name", "benches", "ok"), GOLDEN_CASES)
def test_zone_name_grammar(name: str, benches: tuple[str, ...], ok: bool) -> None:
    assert (zone_name_error(name, benches) is None) is ok


@pytest.mark.parametrize(("curve", "benches", "shared", "expected"), DEFAULT_CASES)
def test_default_zone_name(
    curve: str, benches: tuple[str, ...], shared: bool, expected: str | None
) -> None:
    assert default_zone_name(curve, benches, shared) == expected


def test_defaults_always_pass_the_grammar() -> None:
    for curve, benches, shared, _ in DEFAULT_CASES:
        got = default_zone_name(curve, benches, shared)
        if got is not None:
            assert zone_name_error(got, benches) is None, (curve, got)


class _Session:
    def __init__(self, names: dict[uuid.UUID, str]) -> None:
        self._names = names

    def get(self, _model: object, key: uuid.UUID) -> SimpleNamespace | None:
        name = self._names.get(key)
        return SimpleNamespace(name=name) if name is not None else None


def _spec(tc: uuid.UUID, benches: list[str], name: str | None = None) -> BlueOxZoneSpec:
    return BlueOxZoneSpec(type_curve_id=tc, zone_name=name, reserve_category="PUD", benches=benches)


def test_resolve_derives_from_benches_not_curve_name() -> None:
    """The Snare shape: a single WCB_2 curve named with deal prefix +
    version tag resolves to the bare bench, and a shared bench lifts the
    compass suffix from each curve name."""
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = _Session({a: "snare_wcb_1_uf", b: "RALLYCAPS_WCB_2_NW", c: "RALLYCAPS_WCB_2_SE_v2"})
    zones = [_spec(a, ["WCB_1"]), _spec(b, ["WCB_2"]), _spec(c, ["WCB_2"])]
    _resolve_zone_names(session, zones, fallback_to_curve=False)  # type: ignore[arg-type]
    assert [z.zone_name for z in zones] == ["WCB_1", "WCB_2_NW", "WCB_2_SE"]
    assert _zone_name_errors(zones) == []


def test_unresolvable_name_is_an_error_on_save_but_renders_for_display() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    names = {a: "toucan_bs3c", b: "theCan_bs3c"}  # shared bench, no suffix
    zones = [_spec(a, ["BS3_C"]), _spec(b, ["BS3_C"])]
    _resolve_zone_names(_Session(names), zones, fallback_to_curve=False)  # type: ignore[arg-type]
    errs = _zone_name_errors(zones)
    assert len(errs) == 2 and all("no zone name" in e for e in errs)

    shown = [_spec(a, ["BS3_C"]), _spec(b, ["BS3_C"])]
    _resolve_zone_names(_Session(names), shown, fallback_to_curve=True)  # type: ignore[arg-type]
    assert [z.zone_name for z in shown] == ["toucan_bs3c", "theCan_bs3c"]


def test_typed_names_are_kept_and_checked() -> None:
    """An explicit name is never overwritten; the Snare typo is refused."""
    a = uuid.uuid4()
    zones = [_spec(a, ["WCB_2"], "W CB_2")]
    _resolve_zone_names(_Session({a: "snare_wcb_2_v2"}), zones, fallback_to_curve=False)  # type: ignore[arg-type]
    assert zones[0].zone_name == "W CB_2"
    (err,) = _zone_name_errors(zones)
    assert "no spaces" in err
