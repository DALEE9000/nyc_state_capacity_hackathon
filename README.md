# TIDELINE — NYC Flood-Resilience Capital Tracker

Built by David A. Lee, Dean Berkowitz, and Lyndsey Kaplan for the State Capacity and AI Hackathon on June 24, 2026.

An interactive dashboard that connects **where New York floods** to **where the city spends
its flood-resilience capital dollars**, down to the community-district level — and shows where
those two things don't line up.

It is a single self-contained page (`index.html`) backed by three precomputed JSON files. This
README documents every data source, join, and metric used to reach the conclusions in the tool,
with extra detail on the **flood funding vs. flood burden** analysis (the "Districts" tab), since
that is the most interpretation-heavy claim the dashboard makes.

---

## 1. What the dashboard shows

| Tab | Question it answers | Key visual |
|-----|--------------------|------------|
| **Map** | Where is every capital project, and is it flood-aligned? | Dots colored by alignment, sized & brightened by committed spend; flood / non-flood filter |
| **The Stakes** | How much, how many, where? | Headline numbers + spend-by-category breakdown |
| **Districts** | Which community districts flood most, and do they get matching funding? | Choropleth (funding-gap / flooding modes) + flooding-vs-funding scatter + ranked list |
| **May 20 Storm** | What did a real flash-flood look like in real time? | Time-animated FloodNet depth playback with a rain effect |

---

## 2. Data sources

All inputs are open NYC datasets, snapshotted 2026-06-24:

1. **Capital Projects Database (CPDB) — Projects (Points)** — one geolocated point (or multipoint)
   per capital project, with agency and a `maprojid` key. *Provides geography.*
2. **NYC Climate Budgeting Report** (~260k rows) — every capital funding line tagged with
   climate-alignment ratings (GHG / flood / heat), tracking categories, vulnerability indices,
   fiscal year, and dollar amount. *Provides money + flood ratings.*
3. **Community Districts** — 71 community-district polygons (`BoroCd`, `the_geom`). *Provides district geography.*
4. **FloodNet — Sensor Deployment Metadata** — 453 street-flooding sensors with lat/lon and a
   `CommunityBoard` code. *Maps sensors to districts.*
5. **FloodNet — Street Flooding Events** — 2,448 measured flood events (depth, duration, and a
   per-event depth time series). *Provides the flooding burden + the storm animation.*
6. **MTA Subway GTFS (static)** — the scheduled subway timetable, routes, shapes, and stops.
   *Drives the animated subway trains in the storm tab.* (Downloaded to `data/gtfs_subway.zip`.)
7. **MTA Bus GTFS (static, 6 borough/operator feeds)** — scheduled bus timetables and stops.
   *Drives the animated buses in the storm tab.* (Downloaded to `data/gtfs_bus_*.zip`.)

---

## 3. Build pipeline

Each script (in `scripts/`) reads the raw CSVs from `data/` and writes one JSON to the repo root,
where the page fetches it. Re-run any script after the source data changes. Scripts anchor their
paths to the repo root, so they work from any working directory.

```
scripts/build_dashboard_data.py   ->  dashboard_data.json   (project points, headline, breakdown)
scripts/build_district_data.py    ->  district_data.json    (district polygons + flood/funding stats)
scripts/build_storm_data.py       ->  storm_data.json       (May 20 2026 animated flood event)
scripts/build_storm_trains.py     ->  storm_trains.json     (subway trains + routes + storm flags)
scripts/build_storm_buses.py      ->  storm_buses.json      (buses + storm bus-route flags)
scripts/build_climate_budget_geolocated.py -> data/climate_budget_geolocated.csv  (flat geolocated CSV, standalone)
```

`build_storm_trains.py` / `build_storm_buses.py` read `storm_data.json`, so run them after
`build_storm_data.py`. They auto-download the MTA GTFS to `data/gtfs_subway.zip` and
`data/gtfs_bus_*.zip` if missing.

First put the five raw NYC CSVs in `data/` (they're gitignored — see `data/.gitkeep` for the list),
then from the repo root:

```bash
python scripts/build_dashboard_data.py
python scripts/build_district_data.py
python scripts/build_storm_data.py
python scripts/build_storm_trains.py   # auto-downloads MTA subway GTFS to data/ if missing
python scripts/build_storm_buses.py    # auto-downloads MTA bus GTFS (6 feeds) to data/ if missing
python -m http.server 8000          # then open http://localhost:8000
```

The page uses `fetch`, which browsers block over `file://`, so it must be served over HTTP.

---

## 4. Core joins & definitions (shared by all tabs)

### 4.1 Project ↔ budget join
CPDB `maprojid` (e.g. `039LQMTGRMS`) and the budget's `Project Id` (e.g. `035 L21FREEZE`) are the
same agency-prefixed code, but the budget inserts a space. We join on a **whitespace-stripped,
uppercased** key. This matches **2,161 of 2,747** CPDB projects; the rest aren't in the climate
report (i.e. not tracked as climate investments). Matching on the project suffix alone added only
one extra match, confirming the agency-prefixed key is correct.

### 4.2 Single snapshot (avoiding double-counting)
The climate report contains **three independent publication snapshots** (Published Date × Financial
Plan). The same dollars appear in each, so summing across them multiplies funding. **Every figure in
the dashboard uses the single latest snapshot:** Published Date `05/12/2026`, *FY2026 Executive
Budget*. `build_climate_budget_geolocated.py` retains all snapshots (as columns) for users who want
to compare revisions.

### 4.3 Money units
Budget amounts are published in **USD thousands**; we keep that unit in the JSON and format to
`$M` / `$B` in the UI. Per-project spend = sum of `Fiscal Year Amount` across all fiscal years in the
snapshot.

### 4.4 Flood classifications (from the budget's own ratings)
- **Flood-aligned** = `Flood Resiliency` ∈ {`Aligned`, `Aligned Component`}. (Other values —
  `No Impact`, `Pending Rating`, `Not Rated`, `Special…`, `Missed Opportunity` — are treated as not aligned.)
- **Flood-vulnerable** = `Flood Vulnerability Index` = `true`.
- **Spend-by-category breakdown** = sum of `Fiscal Year Amount` grouped by
  `Flood Resiliency Tracking Category` (e.g. *Combined Sewer Overflow Management*, *Green
  Infrastructure*), dropping nulls and the `"0"` placeholder.

---

## 5. Flood funding vs. burden — full methodology  ⭐

This is the analysis behind the **Districts** tab — the choropleth, the scatter, the ranked list,
and the "funding equity gap" insight. It answers: *do the districts that flood the most receive
proportional flood-resilience funding?* Read this section before quoting those conclusions.

### 5.1 Unit of analysis
The **community district** (71 of them). FloodNet's `CommunityBoard` code and the Community
Districts `BoroCd` use the **same borough-prefixed coding** (e.g. `305` = Brooklyn CD 5), so they
join directly with no normalization. Borough is decoded from the first digit
(1 Manhattan · 2 Bronx · 3 Brooklyn · 4 Queens · 5 Staten Island); codes ≥ 64 are special
park/airport joint-interest areas and are labeled as such.

### 5.2 Flooding **burden** per district
From the 2,448 FloodNet events, joined to sensor metadata by `Sensor ID` (100% match) to get the
`CommunityBoard`, we aggregate per district:
- **`flood_events`** — count of measured flood events *(the primary burden metric)*
- `severe_events` — events with peak depth ≥ 12 inches
- `max_depth_avg` — mean peak depth (inches)
- `flood_minutes` — total flooding duration
- `sensors` — number of FloodNet sensors deployed in the district

We use **event count** as the headline burden measure because it is the most robust to outliers and
the most intuitive ("how often does this neighborhood flood"). 52 of 71 districts have ≥ 1 measured
event.

### 5.3 Flood **funding** per district (point-in-polygon)
Each geolocated capital project (latest snapshot) is assigned to a district by **point-in-polygon**
test (Shapely; an R-tree pre-filters candidate polygons by bounding box, then an exact `contains`
check). Multipoint projects use their **centroid**. Per district we then sum:
- **`flood_aligned_k`** — committed $ on **flood-aligned** projects *(the funding metric)*
- `total_spend_k`, `n_projects` — all projects, for context.

### 5.4 The adequacy metric (the "gap")
We compare burden to funding on a common 0–1 scale:

```
need = flood_events      / max(flood_events  across districts)     # normalized flooding burden
fund = flood_aligned_k   / max(flood_aligned_k across districts)   # normalized flood funding
gap  = fund - need
```

`gap < 0` ⇒ a district floods more than its funding share would suggest (under-served); `gap > 0`
⇒ funding outweighs measured flooding. Districts are bucketed for the choropleth and badges:

| Status | Rule | Meaning |
|--------|------|---------|
| **Underfunded** | `gap < −0.15` | High flooding, comparatively low flood funding |
| **Balanced** | `−0.15 ≤ gap ≤ 0.15` | Funding roughly tracks flooding |
| **Well-funded** | `gap > 0.15` | Flood funding outweighs measured flooding |
| **No flood data** | `flood_events == 0` | No FloodNet-measured flooding to compare against |

The choropleth shades by status (or by raw flooding in "Flooding" mode); fill opacity scales with
flooding intensity so high-burden districts are visually dominant. The scatter plots `flood_events`
(x) against `flood_aligned_k` (y) — the **lower-right quadrant = floods a lot, funded little**.

### 5.5 What the data shows
With this method, the most flood-burdened districts (Queens CD14 / Rockaways, Queens CD10 / Howard
Beach, Bronx CD10, Queens CD13) record by far the most street flooding yet fall in the
**underfunded** bucket, while a lower-flooding district (Brooklyn CD6 / Gowanus–Red Hook) carries the
single largest flood-aligned allocation. That divergence is the dashboard's headline equity finding.

### 5.6 Limitations — read before citing
This is a **screening signal, not a verdict on funding fairness.** Specifically:
- **Sensor coverage is uneven.** FloodNet is a growing network; districts with more/older sensors
  record more events. Event count partly reflects *measurement*, not just *flooding*. Some sensors
  were installed in 2026 and have short records.
- **"Funding" counts only flood-aligned capital, by project point location.** A project's mapped
  point may not be where its benefit lands (e.g. a sewer trunk or a coastal barrier protects areas
  beyond its centroid). Large area-wide projects are attributed to a single district.
- **Need ≠ flooding events alone.** True flood need also depends on population, asset value, tidal
  exposure, and topography, which this metric does not weigh.
- **Normalization is relative.** `need`/`fund` are scaled to the city maximum, so the gap describes
  *relative* standing among districts, not an absolute dollar shortfall.
- **One budget snapshot.** Funding reflects the FY2026 Executive Budget only; future plans may shift allocations.

Treat "underfunded" as *"worth a closer look,"* not a definitive funding judgment.

---

## 6. May 20, 2026 storm animation — methodology

`build_storm_data.py` reconstructs a real flash-flood event for playback:
- Selects all FloodNet events whose start or end falls on **2026-05-20** → 105 events across 97
  sensors; window 2026-05-20 01:43 → 2026-05-21 10:38 GMT; peak depth **46.1 in**; up to **68 sensors
  flooding at once** (~23:35 GMT).
- For each event it parses the per-event **depth time series** (`Time Series Depth Values` /
  `…Timestamps (seconds)`), downsamples to ≤ 48 points (always keeping first, last, and peak), and
  stores the flood-start offset from the window start.
- A **timeline curve** is precomputed by binning the window into 140 intervals and, in each bin,
  counting sensors whose interpolated depth exceeds 0.3 in plus the max depth.
- The frontend animates a storm clock (full event compressed to ~30 s), **linearly interpolating**
  each sensor's depth at the current time. Dot radius and color (cyan → red) encode live depth; the
  **rain effect's density and speed are driven by the same intensity curve**, so it pours hardest at
  the storm's peak.

### 6b. Subway trains in the storm — methodology & honest caveats  ⚠️

The storm tab also animates ~10,500 subway trains and flags lines disrupted by flooding
(`build_storm_trains.py` → `storm_trains.json`). **Read this before describing it as "real-time."**

- **Why it is the *scheduled* timetable, not a live replay.** MTA's real-time (GTFS-realtime)
  feeds are **present-tense only** — there is no API that returns historical train positions for a
  past date, and the static GTFS feed is forward-looking (its calendar starts 2026-05-26, so it does
  not even contain 2026-05-20). May 20, 2026 was a **Wednesday**, and the subway weekday timetable is
  highly stable, so we apply the feed's **Weekday** service pattern to that date. Train motion is the
  *scheduled* weekday service — **real routes, real stations, real timetable — but not a recording of
  what actually ran that night.**
- **Reconstruction.** We take all `Weekday` trips, join `stop_times` → `stops` for each trip's
  station sequence and times, convert GTFS local times (EDT, UTC−4) to the storm's UTC clock,
  instantiate the pattern across the service days the window spans (May 19–21), and keep trips
  overlapping the window. Each trip is downsampled to ≤ 12 `(time, lat, lon)` points; the frontend
  **linearly interpolates** position along the route as the storm clock advances.
- **Route geometry.** The faint base lines tracing each route come from GTFS `shapes.txt` — 228
  weekday line shapes, Ramer–Douglas–Peucker–simplified (~25 m tolerance) to ~8.5k points total,
  drawn in each line's official color beneath the trains.

### 6c. Buses (`build_storm_buses.py` → `storm_buses.json`)

Same approach and same caveat as the trains, for all six MTA bus GTFS feeds (5 boroughs + MTA Bus
Company). Bus GTFS uses depot-prefixed weekday service IDs with school-day variants, which we
**de-duplicate** (collapsing `-SDon`/`-SDoff` and preferring school-day-on, since May 20 is a school
day) before applying the weekday pattern to that date. Citywide weekday bus service is enormous
(~65k trip-instances over the window), so trips are **thinned to ~11,000** (random sample, fixed
seed) to keep the file (~2.5 MB) and the in-browser animation manageable. Affected bus routes use the
same proximity rule (a stop within 300 m of a sensor flooding >12 in); **33 routes** get flagged over
the event, up to **26 at once**, and their **specific route names** are listed in the Service-alert
panel (affected buses also turn red on the map).

**Storm-tab UI controls:** independent **Subway / Buses** toggles (the subway toggle also hides the
route lines), and a **playback-speed** control (0.5× / 1× / 2× / 4×).
- **Disruption flagging (flag-only, by design).** A line is flagged "affected" in a given moment when
  one of its stations is within **300 m** of a FloodNet sensor whose interpolated depth exceeds
  **12 inches** (track-level flooding) at that time. This **only flags** lines — affected trains pulse
  with a red ring and appear in the "Service alert" panel, but their movement is *not* altered (the
  chosen design). 12 lines get flagged over the event; up to **8 simultaneously** at the peak
  (2, 3, 4, 5, D, N, R, W). The flag is a **proximity heuristic**, not a record of actual MTA service
  changes that night (which, again, no API provides after the fact).

---

## 7. Files

```
index.html                  The dashboard (serve over HTTP)
dashboard_data.json         Project points, headline numbers, category breakdown   (committed)
district_data.json          District polygons + flooding/funding stats             (committed)
storm_data.json             May 20 2026 animated flood event                       (committed)
storm_trains.json           Subway trains + route geometry + storm flags           (committed, ~3.6 MB)
storm_buses.json            Buses + storm bus-route flags                          (committed, ~2.5 MB)
README.md  environment.yml  .gitignore

scripts/                    Build pipeline (run from repo root)
  build_dashboard_data.py             -> dashboard_data.json
  build_district_data.py              -> district_data.json   (flood-vs-funding analysis)
  build_storm_data.py                 -> storm_data.json
  build_storm_trains.py               -> storm_trains.json    (MTA GTFS subway animation)
  build_storm_buses.py                -> storm_buses.json     (MTA GTFS bus animation)
  build_climate_budget_geolocated.py  -> data/climate_budget_geolocated.csv (standalone flat export)

notebooks/                  Exploratory notebooks (incl. a Plotly map in the budget notebook)
  explore_capital_projects.ipynb  explore_climate_budget.ipynb  explore_floodnet.ipynb

data/                       Raw NYC CSVs + gtfs_subway.zip + large derived CSV  (gitignored; see data/.gitkeep)
```

## 8. Reproducibility notes
- Dependencies: `pandas`, `shapely` (see `environment.yml`; shapely is required by
  `build_district_data.py`). The frontend loads Leaflet, Chart.js, and CARTO dark basemap tiles from
  CDNs, so the Map / Districts / Storm tabs need internet for tiles.
- All dollar figures are USD **thousands** as published; the UI converts to `$M`/`$B`.
- Deep links: `#map` `#breakdown` `#districts` `#storm`; `?filter=flood|nonflood` on the map;
  `?st=<0–1>` jumps the storm clock.
