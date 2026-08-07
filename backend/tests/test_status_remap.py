"""Warehouse status remap ↔ app enum drift guard — DB-free unit tests.

The remap once emitted "DUC" while WellStatus had no such member, so
~2,900 Spud/DUC wells silently landed as UNKNOWN via _validated_status.
These tests make that drift a test failure instead of a log warning.
"""

from __future__ import annotations

from app.db.models import WellStatus
from app.warehouse_client.wells import _STATUS_REMAP, _status_from_curated


def test_every_remap_target_is_a_known_status() -> None:
    known = {m.value for m in WellStatus}
    for novi_value, app_value in _STATUS_REMAP.items():
        assert app_value in known, (
            f"_STATUS_REMAP[{novi_value!r}] = {app_value!r} is not a "
            "WellStatus member — wells with this status would collapse "
            "to UNKNOWN at ingest"
        )


def test_spud_and_duc_map_to_duc() -> None:
    assert _status_from_curated("Spud") == WellStatus.DUC.value
    assert _status_from_curated("DUC") == WellStatus.DUC.value


def test_unmapped_and_null_collapse_to_unknown() -> None:
    assert _status_from_curated(None) == WellStatus.UNKNOWN.value
    assert _status_from_curated("Permit Approved") == WellStatus.UNKNOWN.value
