"""
Build dashboard_data.json for the flood-resiliency dashboard frontend.

Joins CPDB geolocations to the climate budget (latest snapshot) and emits, per
geolocated project: name, agency, lon/lat, total spend, flood-alignment,
flood-vulnerability, and flood tracking category. Also precomputes the headline
numbers and the spending-by-flood-category breakdown.
"""
import json
import re
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent     # repo root
DATA = ROOT / "data"                               # raw CSVs (gitignored)
CPDB = str(DATA / "Capital_Projects_Database_(CPDB)_-_Projects_(Points)_20260624.csv")
CB = str(DATA / ("NYC_Climate_Budgeting_Report__Climate_Alignment_Assessment_and_"
                 "Capital_Climate_Investments_20260624.csv"))
OUT = str(ROOT / "dashboard_data.json")            # fetched by index.html

POINT_RE = re.compile(r"-?\d+\.\d+\s+-?\d+\.\d+")
ALIGNED = {"Aligned", "Aligned Component"}


def norm_id(s):
    return re.sub(r"\s+", "", str(s)).upper()


def centroid(geometry):
    pts = POINT_RE.findall(str(geometry))
    if not pts:
        return (None, None)
    lon = sum(float(p.split()[0]) for p in pts) / len(pts)
    lat = sum(float(p.split()[1]) for p in pts) / len(pts)
    return (round(lon, 5), round(lat, 5))


def to_num(s):
    return pd.to_numeric(
        s.astype(str).str.replace(",", "", regex=False).str.strip(),
        errors="coerce")


def first_valid(s):
    s = s.dropna()
    return s.iloc[0] if len(s) else None


def main():
    # Geolocations
    cp = pd.read_csv(CPDB, dtype=str, usecols=["maprojid", "geometry"])
    coords = cp["geometry"].map(centroid)
    cp["lon"] = [c[0] for c in coords]
    cp["lat"] = [c[1] for c in coords]
    cp["join_id"] = cp["maprojid"].map(norm_id)
    geo = cp[["join_id", "lon", "lat"]].drop_duplicates("join_id")

    # Climate budget — latest snapshot only
    cb = pd.read_csv(CB, dtype=str)
    cb["join_id"] = cb["Project Id"].map(norm_id)
    cb["amt"] = to_num(cb["Fiscal Year Amount"])
    latest = cb["Published Date"].max()
    cb = cb[cb["Published Date"] == latest].copy()
    plan = cb["Financial Plan"].iloc[0]
    print("Snapshot:", latest, "|", plan)

    cat_col = "Flood Resiliency Tracking Category"
    cb["flood_cat"] = cb[cat_col].where(~cb[cat_col].isin(["0"]))  # drop "0"
    cb["aligned"] = cb["Flood Resiliency"].isin(ALIGNED)
    cb["vulnerable"] = cb["Flood Vulnerability Index"].astype(str).str.lower().eq("true")

    # Per-project aggregation
    proj = cb.groupby("join_id").agg(
        name=("Project Description", first_valid),
        agency=("Budget Line Title", first_valid),
        total_spend=("amt", "sum"),
        aligned=("aligned", "max"),
        vulnerable=("vulnerable", "max"),
        flood_cat=("flood_cat", first_valid),
    ).reset_index()

    # Pull a cleaner agency name from CPDB
    agn = pd.read_csv(CPDB, dtype=str, usecols=["maprojid", "magenname"])
    agn["join_id"] = agn["maprojid"].map(norm_id)
    agn = agn[["join_id", "magenname"]].drop_duplicates("join_id")
    proj = proj.merge(agn, on="join_id", how="left")
    proj["agency"] = proj["magenname"].fillna(proj["agency"])

    proj = proj.merge(geo, on="join_id", how="inner")
    proj = proj.dropna(subset=["lon", "lat"])

    # Build point list (only projects with non-negative meaningful spend kept on map;
    # keep all geolocated projects but round spend)
    proj["total_spend"] = proj["total_spend"].fillna(0).round(0)
    points = [{
        "id": r.join_id,
        "name": (r.name or "(no description)"),
        "agency": (r.agency or "Unknown agency"),
        "lon": r.lon, "lat": r.lat,
        "spend": float(r.total_spend),         # $ thousands
        "aligned": bool(r.aligned),
        "vulnerable": bool(r.vulnerable),
        "cat": (r.flood_cat if isinstance(r.flood_cat, str) else None),
    } for r in proj.itertuples(index=False)]

    # Headline numbers
    aligned_proj = proj[proj["aligned"]]
    headline = {
        "flood_aligned_dollars_k": float(aligned_proj["total_spend"].clip(lower=0).sum()),
        "active_projects": int((proj["total_spend"] > 0).sum()),
        "flood_vulnerable_projects": int(proj["vulnerable"].sum()),
        "flood_aligned_projects": int(proj["aligned"].sum()),
    }

    # Spending-by-flood-category breakdown ($ thousands), from row-level amounts
    bd = (cb.dropna(subset=["flood_cat"])
            .groupby("flood_cat")["amt"].sum()
            .sort_values(ascending=False))
    bd = bd[bd > 0]
    breakdown = [{"category": k, "spend": float(round(v))} for k, v in bd.items()]

    data = {
        "meta": {"snapshot": latest, "financial_plan": plan,
                 "units": "USD thousands", "n_points": len(points)},
        "headline": headline,
        "breakdown": breakdown,
        "points": points,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))

    print(f"Wrote {OUT}: {len(points):,} points")
    print("Headline:", headline)
    print("Breakdown categories:", len(breakdown))
    for b in breakdown[:8]:
        print(f"   {b['category']:<40} ${b['spend']/1e6:,.2f}B")


if __name__ == "__main__":
    main()
