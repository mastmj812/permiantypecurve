"""Deterministic synthetic Enverus data source.

Same interface as PrismClient — drops into `sync_county(client=...)` with
zero changes elsewhere, so every ingest path (header upsert, heel + wellstick
recomputation, calday/prodday rate calc, month-1 exception, watermark
bookkeeping) is exercised on the synthetic data.

Goals:
  * Visually plausible map: ~50 wells scattered across Loving County, TX
    with realistic lateral azimuths and lengths, formation-grouped colors.
  * Forecastable production: hyperbolic decline with qi scaled by lateral
    length so per-foot type curves are non-degenerate.
  * Exercises the awkward cases:
      - month-1 partial-month producing_days (always set 12–26 days)
      - one well per formation has no survey (forces surface_to_bh)
      - one well has only sub-80° survey stations (forces fallback)
      - one well has a survey station with missing lat/lon (must be skipped)

Determinism: a single `random.Random(seed)` drives everything; same seed →
same well set → reproducible regressions across runs.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Iterator
from datetime import date, datetime

from app.enverus_client.base import (
    DirectionalSurvey,
    EnverusClient,
    ProductionRecord,
    SurveyStation,
    WellHeader,
)
from app.ingest.rates import calendar_days_in

# Per-county geographic envelopes + 3-digit TX FIPS code. Tight enough
# that wells don't sprawl into neighboring counties on the map.
# api14 = state-FIPS (42 for TX) + county-FIPS (3 digits) + 5-digit well
# num + 4-digit suffix → ``f"42{fips}{n:05d}0000"``.
#
# Loving County  (TX 48-301, API state-county prefix 42-301): the
#   original sandbox. ~120 producing wells.
# Reeves County  (TX 48-389, API state-county prefix 42-389): the bigger
#   neighbor to the south; thousands of wells in real data, kept under
#   the same n_wells cap here for synthetic mode.
COUNTY_BOUNDS: dict[str, dict[str, float | str]] = {
    "Loving": {
        "fips": "301",
        "lat_min": 31.62, "lat_max": 32.00,
        "lon_min": -104.05, "lon_max": -103.40,
    },
    "Reeves": {
        "fips": "389",
        "lat_min": 31.00, "lat_max": 31.85,
        "lon_min": -104.20, "lon_max": -103.30,
    },
}

# Clearly-synthetic operator names so nobody mistakes these for real wells.
SYNTHETIC_OPERATORS = (
    "Synthetic Op A LLC",
    "Synthetic Op B Energy",
    "Synthetic Op C Resources",
    "Synthetic Op D Operating",
    "Synthetic Op E Permian",
)
DELAWARE_FORMATIONS = (
    "Wolfcamp A",
    "Wolfcamp B",
    "Bone Spring 2nd",
    "Bone Spring 3rd",
)
# Permian unconventional laterals run roughly NW-SE or NE-SW with section
# orientations. Picking from a handful gives a recognizable PLSS look.
AZIMUTHS_DEG = (135.0, 315.0, 45.0, 225.0)

# Rough conversion factors near 32°N.
_FT_PER_DEG_LAT = 364_000.0


def _ft_per_deg_lon(lat_deg: float) -> float:
    return math.cos(math.radians(lat_deg)) * _FT_PER_DEG_LAT


def _offset_latlon(
    lat: float, lon: float, *, distance_ft: float, azimuth_deg: float
) -> tuple[float, float]:
    """Translate (lat, lon) by `distance_ft` along `azimuth_deg` (0=N, 90=E)."""
    az_rad = math.radians(azimuth_deg)
    dn_ft = distance_ft * math.cos(az_rad)
    de_ft = distance_ft * math.sin(az_rad)
    return (
        lat + dn_ft / _FT_PER_DEG_LAT,
        lon + de_ft / _ft_per_deg_lon(lat),
    )


class SyntheticEnverusClient(EnverusClient):
    """A drop-in EnverusClient that yields deterministic Permian-style wells.

    Pre-generates the well registry in __init__ so headers, production, and
    surveys all reference the same well set across method calls (the real
    Enverus would too — the pre-gen here just makes that consistent without
    persistent state).
    """

    def __init__(
        self,
        *,
        n_wells: int = 50,
        county: str = "Loving",
        basin: str = "Permian",
        seed: int = 42,
        as_of: date | None = None,
    ) -> None:
        if county not in COUNTY_BOUNDS:
            known = ", ".join(sorted(COUNTY_BOUNDS))
            raise ValueError(
                f"synthetic client doesn't know county {county!r}; known: {known}"
            )
        self._n = n_wells
        self._county = county
        self._bounds = COUNTY_BOUNDS[county]
        self._basin = basin
        self._rnd = random.Random(seed)
        self._as_of = as_of or date.today()
        self._wells: list[WellHeader] = list(self._generate_wells())
        self._wells_by_api: dict[str, WellHeader] = {w.api14: w for w in self._wells}
        # Persist the edge-case roster: a couple of wells get no survey, one
        # gets a vertical-only survey, one gets a malformed survey.
        self._no_survey = {self._wells[1].api14, self._wells[3].api14}
        self._no_heel_crossover = {self._wells[5].api14}
        self._malformed_first_station = {self._wells[7].api14}

    # ---------- public API ----------
    def fetch_well_headers(
        self,
        *,
        basin: str,
        county: str | None = None,
        updated_since: datetime | None = None,
    ) -> Iterator[WellHeader]:
        for w in self._wells:
            if basin and w.basin != basin:
                continue
            if county is not None and w.county != county:
                continue
            yield w

    def fetch_monthly_production(
        self,
        api14_list: Iterable[str],
        *,
        start_date: date | None = None,
    ) -> Iterator[ProductionRecord]:
        wanted = set(api14_list)
        for api14 in wanted:
            w = self._wells_by_api.get(api14)
            if w is None or w.first_prod_date is None:
                continue
            yield from self._production_for(w, start_date)

    def fetch_directional_survey(self, api14: str) -> DirectionalSurvey | None:
        if api14 in self._no_survey:
            return None
        w = self._wells_by_api.get(api14)
        if w is None or w.sh_lat is None or w.bh_lat is None:
            return None
        stations = list(self._survey_for(w))
        return DirectionalSurvey(api14=api14, stations=stations)

    # ---------- well generator ----------
    def _generate_wells(self) -> Iterator[WellHeader]:
        fips = self._bounds["fips"]
        lat_min = float(self._bounds["lat_min"])
        lat_max = float(self._bounds["lat_max"])
        lon_min = float(self._bounds["lon_min"])
        lon_max = float(self._bounds["lon_max"])
        for i in range(self._n):
            # Texas state FIPS 42 + 3-digit county FIPS + 5-digit well num
            # + 4-digit suffix. (Real TX API14 format is 42-XXX-NNNNN-SS-WW
            # — this matches.) Per-county FIPS keeps the api14 prefix
            # honest when both Loving and Reeves are seeded into one DB.
            api14 = f"42{fips}{i:05d}0000"

            sh_lat = self._rnd.uniform(lat_min, lat_max)
            sh_lon = self._rnd.uniform(lon_min, lon_max)
            azimuth = self._rnd.choice(AZIMUTHS_DEG)
            lateral_ft = self._rnd.choice([5000, 7500, 10000, 10000, 10000, 12000])
            bh_lat, bh_lon = _offset_latlon(
                sh_lat, sh_lon, distance_ft=lateral_ft, azimuth_deg=azimuth
            )

            tvd_ft = self._rnd.uniform(9500, 11500)
            stages = max(15, lateral_ft // 220 + self._rnd.randint(-3, 3))
            # Permian completions: ~2000 lbs/ft proppant, ~40 bbl/ft fluid.
            proppant_lbs = lateral_ft * self._rnd.uniform(1600, 2400)
            fluid_bbl = lateral_ft * self._rnd.uniform(30, 50)

            # First prod scattered 2018-2025, biased toward recent.
            year = self._rnd.choices(
                [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025],
                weights=[1, 1, 1, 2, 3, 4, 4, 3],
                k=1,
            )[0]
            month = self._rnd.randint(1, 12)
            day = self._rnd.randint(1, 28)
            first_prod_date = date(year, month, day)

            # Mimic the operator+lease+number+H pattern typical of Permian
            # unconventional well names (e.g. "STATE 36 #1H"). Purely
            # decorative for synthetic data — there's no underlying lease.
            pad = self._rnd.choice(("STATE", "FEDERAL", "RANCH", "MESA", "BLUFF"))
            well_name = f"SYNTH {pad} {i:03d} #{(i % 8) + 1}H"

            yield WellHeader(
                api14=api14,
                name=well_name,
                operator=self._rnd.choice(SYNTHETIC_OPERATORS),
                formation=self._rnd.choice(DELAWARE_FORMATIONS),
                first_prod_date=first_prod_date,
                lateral_ft=float(lateral_ft),
                proppant_lbs=proppant_lbs,
                fluid_bbl=fluid_bbl,
                stages=int(stages),
                tvd_ft=tvd_ft,
                county=self._county,
                basin=self._basin,
                status="PRODUCING",
                sh_lat=sh_lat,
                sh_lon=sh_lon,
                bh_lat=bh_lat,
                bh_lon=bh_lon,
                raw={"synthetic": True, "azimuth_deg": azimuth, "seed_index": i},
            )

    # ---------- survey generator ----------
    def _survey_for(self, w: WellHeader) -> Iterator[SurveyStation]:
        """3- or 4-station survey. Heel sits ~5% of the way down the lateral.

        Vertical-only edge case: `_no_heel_crossover` returns stations that
        all stay below 80°. Malformed edge case: `_malformed_first_station`
        emits a >=80° station with no lat/lon, then a valid one after.
        """
        assert w.sh_lat is not None and w.sh_lon is not None  # gated by caller
        assert w.bh_lat is not None and w.bh_lon is not None
        tvd = w.tvd_ft or 10000.0

        # Heel coords: a small fraction of the way along the s→b line.
        heel_lat = w.sh_lat + (w.bh_lat - w.sh_lat) * 0.03
        heel_lon = w.sh_lon + (w.bh_lon - w.sh_lon) * 0.03

        if w.api14 in self._no_heel_crossover:
            # Vertical well — never crosses 80°. Should fall back to
            # surface_to_bh in compute_heel.
            yield SurveyStation(
                station_seq=1, md_ft=0.0, inclination_deg=0.0,
                azimuth_deg=0.0, tvd_ft=0.0, lat=w.sh_lat, lon=w.sh_lon,
            )
            yield SurveyStation(
                station_seq=2, md_ft=tvd * 0.5, inclination_deg=2.0,
                azimuth_deg=0.0, tvd_ft=tvd * 0.5, lat=w.sh_lat, lon=w.sh_lon,
            )
            yield SurveyStation(
                station_seq=3, md_ft=tvd, inclination_deg=5.0,
                azimuth_deg=0.0, tvd_ft=tvd, lat=w.sh_lat, lon=w.sh_lon,
            )
            return

        # Surface tie-in.
        yield SurveyStation(
            station_seq=1, md_ft=0.0, inclination_deg=0.0,
            azimuth_deg=0.0, tvd_ft=0.0, lat=w.sh_lat, lon=w.sh_lon,
        )
        # Kickoff (~80% of TVD, still mostly vertical).
        yield SurveyStation(
            station_seq=2, md_ft=tvd * 0.85, inclination_deg=15.0,
            azimuth_deg=0.0, tvd_ft=tvd * 0.85, lat=w.sh_lat, lon=w.sh_lon,
        )

        # Heel station — first crossover of 80°. For the malformed edge case,
        # emit a no-coords heel first; compute_heel should skip it and the
        # NEXT high-incl station wins.
        if w.api14 in self._malformed_first_station:
            yield SurveyStation(
                station_seq=3, md_ft=tvd + 100, inclination_deg=82.0,
                azimuth_deg=0.0, tvd_ft=tvd + 50, lat=None, lon=None,
            )
            yield SurveyStation(
                station_seq=4, md_ft=tvd + 200, inclination_deg=88.0,
                azimuth_deg=0.0, tvd_ft=tvd + 80,
                lat=heel_lat, lon=heel_lon,
            )
            yield SurveyStation(
                station_seq=5, md_ft=tvd + 200 + (w.lateral_ft or 10000),
                inclination_deg=90.0, azimuth_deg=0.0, tvd_ft=tvd + 80,
                lat=w.bh_lat, lon=w.bh_lon,
            )
            return

        yield SurveyStation(
            station_seq=3, md_ft=tvd + 200, inclination_deg=88.0,
            azimuth_deg=0.0, tvd_ft=tvd + 80,
            lat=heel_lat, lon=heel_lon,
        )
        # TD.
        yield SurveyStation(
            station_seq=4, md_ft=tvd + 200 + (w.lateral_ft or 10000),
            inclination_deg=90.0, azimuth_deg=0.0, tvd_ft=tvd + 80,
            lat=w.bh_lat, lon=w.bh_lon,
        )

    # ---------- production generator ----------
    def _production_for(
        self, w: WellHeader, start_date: date | None
    ) -> Iterator[ProductionRecord]:
        """Hyperbolic decline with a 1-3 month ramp-up to peak.

        Permian Wolfcamp/Bone Spring wells typically don't hit peak rate
        immediately — frac fluid recovery, gas break-out, and choke
        management mean month 0 produces at 25-50% of peak rate, month 1
        ramps to 60-85%, and peak is reached around month 1-3. After the
        ramp, hyperbolic decline kicks in from peak.

        GOR and WOR drift up linearly the way real Delaware Basin wells do.
        """
        assert w.first_prod_date is not None
        # Per-well RNG keyed off the seed_index in raw_payload so production
        # is stable across runs even if well order shifts.
        seed_idx = int(w.raw.get("seed_index", 0)) if w.raw else 0
        rnd = random.Random((seed_idx + 1) * 1009)

        lateral = w.lateral_ft or 7500
        # qi at peak month: ~50 BOPD per 1000 ft lateral, with spread.
        qi_bopd = 50.0 * (lateral / 1000) * rnd.uniform(0.7, 1.4)
        di_yr = rnd.uniform(0.7, 1.2)           # nominal Arps Di
        b = rnd.uniform(1.0, 1.5)                # hyperbolic exponent
        gor_start = rnd.uniform(4.0, 9.0)        # mcf/bbl
        wor_start = rnd.uniform(1.5, 4.5)        # bbl water / bbl oil

        # Ramp-up parameters. Most wells reach peak in ~2 months; a few
        # ramp fast (1 month) or slow (3 months). Month-0 cleanup fraction
        # spans 0.25–0.50 of peak rate — wide enough to make the
        # well-to-well variation visible in the type-curve band.
        ramp_months = rnd.choice([1, 2, 2, 2, 3])
        m0_fraction = rnd.uniform(0.25, 0.50)

        def _ramp_factor(idx: int) -> float:
            if idx >= ramp_months:
                return 1.0
            # Linear from m0_fraction at idx=0 to 1.0 at idx=ramp_months.
            return m0_fraction + (1.0 - m0_fraction) * (idx / ramp_months)

        d = w.first_prod_date.replace(day=1)
        end = self._as_of
        for month_idx in range(0, 12 * 8):  # up to 8 years
            if d > end:
                break
            if start_date and d < start_date:
                d = _add_month(d)
                continue

            if month_idx < ramp_months:
                # Ramp phase: rate is a fraction of qi. The Arps clock
                # doesn't start until peak is reached.
                rate_bopd = qi_bopd * _ramp_factor(month_idx)
            else:
                # Decline phase: hyperbolic, with t=0 at the end of ramp.
                t_post_peak_years = (month_idx - ramp_months) / 12.0
                rate_bopd = (
                    qi_bopd / (1 + b * di_yr * t_post_peak_years) ** (1.0 / b)
                )

            cal_days = calendar_days_in(d)
            if month_idx == 0:
                # Partial first month — between half and full month online.
                producing_days = rnd.randint(12, 26)
            else:
                # Most months are full; ~5% see a workover with reduced PD.
                producing_days = (
                    cal_days if rnd.random() > 0.05 else rnd.randint(15, cal_days - 1)
                )

            oil_bbl = rate_bopd * producing_days * rnd.uniform(0.92, 1.08)
            # GOR and WOR drift up linearly with time, capped.
            gor = gor_start + month_idx * 0.05
            wor = wor_start + month_idx * 0.08
            gas_mcf = oil_bbl * gor * rnd.uniform(0.95, 1.05)
            water_bbl = oil_bbl * wor * rnd.uniform(0.95, 1.05)

            yield ProductionRecord(
                api14=w.api14,
                prod_date=d,
                oil_bbl=oil_bbl,
                gas_mcf=gas_mcf,
                water_bbl=water_bbl,
                producing_days=producing_days,
                source="synthetic",
            )
            d = _add_month(d)


def _add_month(d: date) -> date:
    if d.month == 12:
        return d.replace(year=d.year + 1, month=1)
    return d.replace(month=d.month + 1)
