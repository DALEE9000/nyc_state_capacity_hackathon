"""
Build district_data.json: per NYC community district, combine
  - FloodNet street-flooding observations (events / depth / duration), and
  - flood-resilience capital funding (projects assigned by point-in-polygon),
plus a simplified polygon for choropleth mapping and a funding-adequacy metric
(does a district's flood funding match its flooding burden?).

Inputs:
  Community_Districts_20260624.csv                     (BoroCd, the_geom MULTIPOLYGON)
  FloodNet__Sensor_Deployment_Metadata_20260624.csv    (Sensor ID, CommunityBoard, lat/lon)
  FloodNet__Street_Flooding_Events_*.csv               (Sensor ID, depth, duration)
  dashboard_data.json                                  (project points: lon/lat/spend/aligned)
"""
import json
from pathlib import Path
import pandas as pd
from shapely import wkt
from shapely.geometry import Point, mapping
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent     # repo root
DATA = ROOT / "data"                               # raw CSVs (gitignored)
CD = str(DATA / "Community_Districts_20260624.csv")
SENS = str(DATA / "FloodNet__Sensor_Deployment_Metadata_20260624.csv")
EVT = str(DATA / "FloodNet__Street_Flooding_Events_Measured_by_FloodNet_Sensors_20260624.csv")
PTS = str(ROOT / "dashboard_data.json")            # produced by build_dashboard_data.py
OUT = str(ROOT / "district_data.json")             # fetched by index.html

BORO = {"1": "Manhattan", "2": "Bronx", "3": "Brooklyn",
        "4": "Queens", "5": "Staten Island"}


def num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False),
                         errors="coerce")


def cd_label(borocd):
    b = BORO.get(borocd[0], "NYC")
    n = int(borocd[1:])
    return f"{b} CD{n}" if n < 64 else f"{b} (parks/airport)"


def main():
    # ---- District polygons ----
    cd = pd.read_csv(CD, dtype=str)
    cd["geom"] = cd["the_geom"].map(wkt.loads)
    # simplify for the web (≈25 m tolerance) to keep the JSON small
    cd["geom_s"] = cd["geom"].map(lambda g: g.simplify(0.00025, preserve_topology=True))
    geoms = list(cd["geom"])
    codes = list(cd["BoroCd"])
    tree = STRtree(geoms)

    dist = {c: {"borocd": c, "name": cd_label(c), "boro": BORO.get(c[0], "NYC"),
                "flood_aligned_k": 0.0, "total_spend_k": 0.0, "n_projects": 0,
                "flood_events": 0, "sensors": 0, "severe_events": 0,
                "max_depth_avg": 0.0, "flood_minutes": 0.0}
            for c in codes}

    # ---- Assign capital projects to districts (point in polygon) ----
    pts = json.load(open(PTS, encoding="utf-8"))["points"]
    for p in pts:
        pt = Point(p["lon"], p["lat"])
        hit = None
        for idx in tree.query(pt):           # candidate polygons by bbox
            if geoms[idx].contains(pt):
                hit = codes[idx]
                break
        if hit is None:
            continue
        d = dist[hit]
        d["n_projects"] += 1
        d["total_spend_k"] += max(p["spend"], 0)
        if p["aligned"]:
            d["flood_aligned_k"] += max(p["spend"], 0)

    # ---- FloodNet: events -> sensor -> community board ----
    sm = pd.read_csv(SENS, dtype=str)
    sm = sm.dropna(subset=["CommunityBoard"])
    sid2cb = dict(zip(sm["Sensor ID"], sm["CommunityBoard"]))
    sensors_per_cb = sm.groupby("CommunityBoard")["Sensor ID"].nunique()
    for cb, n in sensors_per_cb.items():
        if cb in dist:
            dist[cb]["sensors"] = int(n)

    ev = pd.read_csv(EVT, dtype=str)
    ev["cb"] = ev["Sensor ID"].map(sid2cb)
    ev["depth"] = num(ev["Maximum Flood Depth (inches)"])
    ev["dur"] = num(ev["Total Duration (minutes)"])
    ev = ev.dropna(subset=["cb"])
    for cb, g in ev.groupby("cb"):
        if cb not in dist:
            continue
        d = dist[cb]
        d["flood_events"] = int(len(g))
        d["severe_events"] = int((g["depth"] >= 12).sum())
        d["max_depth_avg"] = round(float(g["depth"].mean(skipna=True)), 1)
        d["flood_minutes"] = round(float(g["dur"].sum(skipna=True)), 0)

    rows = list(dist.values())

    # ---- Normalized flood "need" vs flood funding, and the gap ----
    def norm(vals):
        m = max(vals) or 1
        return [v / m for v in vals]

    need_raw = [r["flood_events"] for r in rows]          # flooding burden
    fund_raw = [r["flood_aligned_k"] for r in rows]       # flood $ received
    need_n, fund_n = norm(need_raw), norm(fund_raw)
    for r, ne, fu in zip(rows, need_n, fund_n):
        r["need"] = round(ne, 3)
        r["fund"] = round(fu, 3)
        r["gap"] = round(fu - ne, 3)   # <0 => under-funded relative to flooding
        # adequacy label only meaningful where there is measured flooding
        if r["flood_events"] == 0:
            r["status"] = "no_data"
        elif r["gap"] < -0.15:
            r["status"] = "underfunded"
        elif r["gap"] > 0.15:
            r["status"] = "funded"
        else:
            r["status"] = "balanced"

    # ---- GeoJSON for choropleth ----
    features = []
    stat_by_code = {r["borocd"]: r for r in rows}
    for c, gs in zip(codes, cd["geom_s"]):
        r = stat_by_code[c]
        features.append({
            "type": "Feature",
            "properties": {k: r[k] for k in
                ("borocd", "name", "boro", "flood_events", "severe_events",
                 "max_depth_avg", "sensors", "flood_aligned_k", "total_spend_k",
                 "n_projects", "need", "fund", "gap", "status")},
            "geometry": json.loads(json.dumps(mapping(gs)), parse_float=lambda x: round(float(x), 5)),
        })
    geojson = {"type": "FeatureCollection", "features": features}

    # ---- FloodNet sensor points (for a sensor layer) ----
    sm2 = sm.dropna(subset=["Latitude", "Longitude"])
    sensors = [{
        "name": r["Sensor Name"],
        "lat": round(float(r["Latitude"]), 5),
        "lon": round(float(r["Longitude"]), 5),
        "cb": r["CommunityBoard"],
        "tidal": str(r.get("Tidally Influenced", "")).strip().lower() == "yes",
    } for _, r in sm2.iterrows()]

    out = {
        "districts": sorted(rows, key=lambda r: -r["flood_events"]),
        "geojson": geojson,
        "sensors": sensors,
        "meta": {
            "n_districts": len(rows),
            "n_sensors": len(sensors),
            "total_flood_events": int(ev["cb"].notna().sum()),
        },
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))

    # ---- Report ----
    print(f"Wrote {OUT}")
    import os
    print(f"size: {os.path.getsize(OUT)/1e6:.2f} MB")
    flooded = [r for r in rows if r["flood_events"] > 0]
    print(f"districts with flooding: {len(flooded)} / {len(rows)}")
    print("\nTop flooding districts (events | severe | flood $ aligned | status):")
    for r in sorted(rows, key=lambda r: -r["flood_events"])[:12]:
        print(f"  {r['name']:<26} {r['flood_events']:>4} | {r['severe_events']:>3} | "
              f"${r['flood_aligned_k']/1e6:>5.2f}B | {r['status']}")
    print("\nMost under-funded (high flooding, low flood $):")
    for r in sorted(flooded, key=lambda r: r["gap"])[:8]:
        print(f"  {r['name']:<26} events={r['flood_events']:>4} "
              f"flood$={r['flood_aligned_k']/1e6:.2f}B gap={r['gap']}")


if __name__ == "__main__":
    main()
