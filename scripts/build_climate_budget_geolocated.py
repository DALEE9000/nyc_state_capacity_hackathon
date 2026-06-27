"""
Join NYC Capital Projects Database (CPDB) geolocations to the NYC Climate
Budgeting Report, producing a CSV where each climate-budget data point carries:
  - geolocation (longitude / latitude)
  - fiscal year
  - amount allocated (fiscal year amount, in $ thousands as published)

JOIN KEY
--------
CPDB `maprojid`  e.g. "035L21FREEZE"  (3-digit agency code + project id)
Climate `Project Id` e.g. "035 L21FREEZE" (same, with a space)
=> normalize by removing whitespace + uppercasing, then match.

GRAIN / DOUBLE-COUNTING
-----------------------
The climate report contains three independent publication snapshots
(Published Date x Financial Plan). Summing across them would multiply the
same dollars. We therefore keep Published Date + Financial Plan as columns,
and aggregate (sum) Fiscal Year Amount over Budget Lines within
(Published Date, Financial Plan, project, Fiscal Year). Filter to a single
snapshot (e.g. the latest Published Date) to get one clean financial plan.
"""

import re
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent     # repo root
DATA = ROOT / "data"                               # raw CSVs (gitignored)
CPDB = str(DATA / "Capital_Projects_Database_(CPDB)_-_Projects_(Points)_20260624.csv")
CB = str(DATA / ("NYC_Climate_Budgeting_Report__Climate_Alignment_Assessment_and_"
                 "Capital_Climate_Investments_20260624.csv"))
OUT = str(DATA / "climate_budget_geolocated.csv")  # large derived CSV (gitignored)

POINT_RE = re.compile(r"-?\d+\.\d+\s+-?\d+\.\d+")


def norm_id(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).upper()


def centroid(geometry: str):
    """Return (lon, lat, n_points) centroid of a MULTIPOINT WKT string."""
    pts = POINT_RE.findall(str(geometry))
    if not pts:
        return (None, None, 0)
    lons, lats = [], []
    for p in pts:
        lon, lat = p.split()
        lons.append(float(lon))
        lats.append(float(lat))
    n = len(lons)
    return (sum(lons) / n, sum(lats) / n, n)


def to_num(series: pd.Series) -> pd.Series:
    """'2,025' -> 2025 ; '393' -> 393 ; '' -> NaN (strip thousands commas)."""
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.strip(),
        errors="coerce",
    )


def main():
    # --- Geolocations from CPDB ---------------------------------------------
    cp = pd.read_csv(
        CPDB, dtype=str,
        usecols=["maprojid", "magency", "magenname", "projectid",
                 "descript", "typecat", "geometry"],
    )
    coords = cp["geometry"].map(centroid)
    cp["longitude"] = [c[0] for c in coords]
    cp["latitude"] = [c[1] for c in coords]
    cp["n_points"] = [c[2] for c in coords]
    cp["join_id"] = cp["maprojid"].map(norm_id)
    geo = cp[["join_id", "maprojid", "magency", "magenname",
              "longitude", "latitude", "n_points"]].drop_duplicates("join_id")

    # --- Climate budget spending --------------------------------------------
    cb = pd.read_csv(CB, dtype=str)
    cb["join_id"] = cb["Project Id"].map(norm_id)
    cb["fiscal_year"] = to_num(cb["Fiscal Year"]).astype("Int64")
    cb["fiscal_year_amount"] = to_num(cb["Fiscal Year Amount"])

    # Aggregate over budget lines within each snapshot + project + fiscal year
    grp_cols = ["Published Date", "Financial Plan", "join_id", "fiscal_year"]
    meta_cols = {
        "Project Id": "first",
        "Project Description": "first",
        "Asset Category": "first",
        "Greenhouse Gas (GHG) Mitigation": "first",
        "Flood Resiliency": "first",
        "Heat Resiliency": "first",
        "Heat Vulnerability Index": "first",
        "Flood Vulnerability Index": "first",
        "fiscal_year_amount": "sum",
    }
    cb_agg = cb.groupby(grp_cols, dropna=False).agg(meta_cols).reset_index()

    # --- Join geolocation ----------------------------------------------------
    out = cb_agg.merge(geo, on="join_id", how="inner")

    out = out.rename(columns={
        "Published Date": "published_date",
        "Financial Plan": "financial_plan",
        "Project Id": "project_id_raw",
        "Project Description": "project_description",
        "Asset Category": "asset_category",
        "Greenhouse Gas (GHG) Mitigation": "ghg_mitigation",
        "Flood Resiliency": "flood_resiliency",
        "Heat Resiliency": "heat_resiliency",
        "Heat Vulnerability Index": "heat_vulnerability_index",
        "Flood Vulnerability Index": "flood_vulnerability_index",
        "magency": "agency_code",
        "magenname": "agency_name",
    })

    out = out[[
        "join_id", "maprojid", "project_id_raw",
        "agency_code", "agency_name", "project_description",
        "asset_category",
        "ghg_mitigation", "flood_resiliency", "heat_resiliency",
        "heat_vulnerability_index", "flood_vulnerability_index",
        "published_date", "financial_plan",
        "fiscal_year", "fiscal_year_amount",
        "longitude", "latitude", "n_points",
    ]].sort_values(
        ["published_date", "join_id", "fiscal_year"]
    ).reset_index(drop=True)

    out.to_csv(OUT, index=False)

    # --- Report --------------------------------------------------------------
    print(f"Wrote {OUT}: {len(out):,} rows")
    print(f"Distinct geolocated projects: {out['join_id'].nunique():,}")
    print(f"CPDB projects total: {geo['join_id'].nunique():,}")
    print(f"Climate-budget projects total: {cb['join_id'].nunique():,}")
    print("\nRows per snapshot:")
    print(out.groupby(["published_date", "financial_plan"]).size())
    print("\nLatest snapshot total $ allocated by fiscal year "
          "($ thousands):")
    latest = out[out["published_date"] == out["published_date"].max()]
    print(latest.groupby("fiscal_year")["fiscal_year_amount"].sum())


if __name__ == "__main__":
    main()
