# boxfit

The decline-fitting engine of record, extracted from anduin's `app/forecasting` (BOX type curves,
step 5a — engineering_db `docs/box_type_curves_plan.md` D15). Pure Python: numpy / pandas / scipy /
structlog, no database.

Consumers:
- **anduin** (`backend/`) — installed editable; `app.forecasting.*` and
  `app.type_curves.{aggregate,fit_p50,ratio_mode}` are aliases of the modules here.
- **engineering_db BOX batch** — pinned by commit:
  `blueox-boxfit @ git+https://github.com/mastmj812/permiantypecurve.git@<sha>#subdirectory=backend/packages/boxfit`

| Module | What |
|---|---|
| `types` | `ForecastConfig` / `ForecastResult`, defaults (Df 0.08, Midland 0.06, water Di cap 12/yr, 50-yr horizon) |
| `models`, `metrics`, `cumulative`, `eur` | Arps rate / cum / EUR math (`compute_eur`: closed form / `quad`) |
| `peak_detection` | onset + per-stream peak (first-12-month window, ties → highest actual rate) |
| `fit` | rate-cum / rate-time fits, nominal Di ∈ [0.5, 4.0], b ∈ [0.9, 1.2], at-bound flags, fallback |
| `b_prior` (+ `data/b_priors.json`) | short-history b prior by sub-basin × bench |
| `ramp_arps` | ramp + Arps evaluation; `trapezoid_eur` (600-month grid) = display/export integral |
| `ratio` | GOR / WOR ratio mode vs cumulative oil |
| `well` | `fit_well_streams` — the per-well pipeline (every stream on its own peak) |
| `tc.align`, `tc.aggregate`, `tc.fit_p50`, `tc.ratio_mode` | `peak_ramp` alignment, cohort panels, P50 fit, TC ratio streams |

Distribution name is **`blueox-boxfit`** (import name `boxfit`): an unrelated `boxfit` exists on
PyPI, so never install or depend on it by the bare name.

Behaviour changes go through anduin's suite (real-well baselines + the money test) — this package
has no second copy of the math to keep in sync.
