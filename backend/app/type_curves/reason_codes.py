"""Reason codes for engineer-authored well exclusions.

User-selectable codes for the review-screen exclusion and post-save
membership removals. The build-up sheet groups culls by these codes,
so the set is deliberately small and engineering-meaningful; nuance
goes in the free-text note.

System cull stages (vintage / lateral / filters_other / not_selected /
no_peak / short_history / post_save_removed) are NOT in this dict —
they are derived by the waterfall (buildup.py), never user-authored.
"""

from __future__ import annotations

REVIEW_REASON_CODES: dict[str, str] = {
    "outlier_profile": "Outlier production profile",
    "data_quality": "Data quality",
    "mechanical_downtime": "Mechanical / downtime",
    "parent_child_spacing": "Parent-child / spacing",
    "geology_landing": "Geology / landing zone",
    "other": "Other",
}


def validate_code(code: str) -> str:
    """Return ``code`` if known, else raise ValueError (→ 422 in pydantic)."""
    if code not in REVIEW_REASON_CODES:
        raise ValueError(
            f"unknown reason code {code!r}; expected one of {sorted(REVIEW_REASON_CODES)}"
        )
    return code
