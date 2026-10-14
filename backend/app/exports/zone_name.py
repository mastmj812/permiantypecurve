"""Blue Ox zone-name grammar (Michael 2026-10-14, after the Snare drop
shipped a `W CB_2` tab).

A zone name IS the workbook tab name and Blue Ox's stable zone key, so
it is a formation tag, never a curve name: the ``formation_blueox``
code of one of the zone's benches (``WCB_2``), optionally followed by
an ``_SUFFIX`` that tells apart several zones on one bench (``WCB_2_N``,
``WCB_2_NW``). A multi-bench zone may instead carry the bare family tag
its benches share (``WCA`` for ``WCA_1`` + ``WCA_2``). Letters, digits
and underscores only — no spaces, hyphens, or curve-version tags
(``_v2`` belongs on the anduin curve name).

The frontend mirror ``frontend/src/api/zoneName.ts`` must stay in step;
both test suites pin the same golden table (``GOLDEN_CASES``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

# Names already shipped stay final (contract Principle 2 — stable
# across re-drops); bro_time's §11 split predates this grammar.
GRANDFATHERED_ZONE_NAMES: frozenset[str] = frozenset({"WCB_2 West", "WCB_2 East"})

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_VERSION_RE = re.compile(r"v\d+", re.IGNORECASE)


def zone_name_heads(benches: Iterable[str]) -> set[str]:
    """Tags a zone name may consist of on its own: each bench code plus
    each underscore-prefix of it (``WCA_1`` -> ``WCA_1``, ``WCA``)."""
    heads: set[str] = set()
    for b in benches:
        parts = b.split("_")
        heads.update("_".join(parts[:k]) for k in range(1, len(parts) + 1))
    return heads


def zone_name_error(name: str, benches: Sequence[str]) -> str | None:
    """Why ``name`` is not a valid zone name for a zone mapping
    ``benches``; None when it is. Sheet-name mechanics (length,
    reserved names) are checked separately by the workbook builder."""
    if name in GRANDFATHERED_ZONE_NAMES:
        return None
    if not name:
        return "zone name is empty"
    tokens = name.split("_")
    if not all(_TOKEN_RE.fullmatch(t) for t in tokens):
        return (
            f"zone name {name!r}: letters, digits and single underscores only "
            "(no spaces, hyphens, or leading/trailing/double underscores)"
        )
    if any(_VERSION_RE.fullmatch(t) for t in tokens):
        return (
            f"zone name {name!r}: version tags (_v2) belong on the curve name, "
            "not the zone — the zone name is the formation tag Blue Ox keys on"
        )
    if not benches:
        return None
    if name in zone_name_heads(benches):
        return None
    for b in benches:
        if name.startswith(b + "_"):
            return None
    tags = ", ".join(sorted(benches))
    return (
        f"zone name {name!r} must be the zone's formation_blueox tag ({tags}) "
        "plus an optional _SUFFIX, e.g. WCB_2 or WCB_2_NW"
    )


def _family(benches: Sequence[str]) -> str:
    """Longest underscore-prefix shared by every bench (``WCA_1`` +
    ``WCA_2`` -> ``WCA``); the first bench when they share none."""
    split = [b.split("_") for b in benches]
    common: list[str] = []
    for parts in zip(*split, strict=False):
        if len(set(parts)) != 1:
            break
        common.append(parts[0])
    return "_".join(common) if common else benches[0]


def default_zone_name(curve_name: str, benches: Sequence[str], shared: bool) -> str | None:
    """Suggested zone name for a zone mapping ``benches`` behind the
    curve ``curve_name``.

    Unshared bench -> the bench code (or the family tag of a
    multi-bench zone); the curve name is ignored. A bench another zone
    in the same drop also claims (``shared``) needs a suffix, lifted
    from the curve name after its bench code with version tags dropped
    (``RALLYCAPS_WCB_2_NW_v2`` -> ``WCB_2_NW``). None when there is no
    bench, or a shared bench whose curve name carries no suffix — the
    engineer must type it.
    """
    if not benches:
        return None
    if len(benches) > 1:
        return _family(benches)
    bench = benches[0]
    if not shared:
        return bench
    # Bench code inside the curve name, tolerant of case and of a
    # dropped underscore (broTime_bs1s), bounded by _ or the ends.
    pat = "_?".join(re.escape(p) for p in bench.split("_"))
    m = re.search(rf"(?:^|_)({pat})(?:_|$)", curve_name, re.IGNORECASE)
    if m is None:
        return None
    rest = [t for t in curve_name[m.end(1) :].split("_") if t]
    suffix = [t for t in rest if not _VERSION_RE.fullmatch(t)]
    if not suffix or not all(_TOKEN_RE.fullmatch(t) for t in suffix):
        return None
    return "_".join([bench, *suffix])


# (name, benches, expected-ok) — mirrored verbatim in
# frontend/src/api/zoneName.test.ts. Every live config name except
# Snare's is in here as an OK case (2026-10-14 sweep).
GOLDEN_CASES: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("WCB_2", ("WCB_2",), True),
    ("WCB_2_NW", ("WCB_2",), True),
    ("WCB_2_N", ("WCB_2",), True),
    ("BS3_C_toucan", ("BS3_C",), True),
    ("WDFD_south", ("WDFD",), True),
    ("WCA", ("WCA_1",), True),
    ("AVA", ("AVA_1",), True),
    ("WCA_1", ("WCA_1", "WCA_2"), True),
    ("BS2_S", ("BS2_S", "BS2_C"), True),
    ("WCB_2 West", ("WCB_2",), True),
    ("W CB_2", ("WCB_2",), False),
    ("WCB_2 ", ("WCB_2",), False),
    ("WCB_2-A", ("WCB_2",), False),
    ("WCB_2__N", ("WCB_2",), False),
    ("WCB_2_", ("WCB_2",), False),
    ("WCB_2_v2", ("WCB_2",), False),
    ("snare_wcb_2_v2", ("WCB_2",), False),
    ("WCB_1", ("WCB_2",), False),
    ("WCB_1_N", ("WCB_2",), False),
    ("wcb_2", ("WCB_2",), False),
    ("WCB", ("WCB_2",), True),
)

# (curve_name, benches, shared, expected default)
DEFAULT_CASES: tuple[tuple[str, tuple[str, ...], bool, str | None], ...] = (
    ("snare_wcb_2_v2", ("WCB_2",), False, "WCB_2"),
    ("snare_wcb_1_uf", ("WCB_1",), False, "WCB_1"),
    ("RALLYCAPS_WCB_2_NW", ("WCB_2",), True, "WCB_2_NW"),
    ("rallycaps_WCB_2_SE_v2", ("WCB_2",), True, "WCB_2_SE"),
    ("brotime_wcb_2_west", ("WCB_2",), True, "WCB_2_west"),
    ("toucan_bs3c", ("BS3_C",), True, None),
    ("castaway_wca", ("WCA_1", "WCA_2"), False, "WCA"),
    ("theCan_bs2", ("BS2_S", "BS2_C"), False, "BS2"),
    ("anything", (), False, None),
)
