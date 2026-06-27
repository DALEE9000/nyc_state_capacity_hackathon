"""
Build storm_trains.json: animate NYC subway trains across the May 20, 2026 storm
window and flag the lines disrupted by flooding.

DATA NOTE — why this is the *scheduled* timetable, not a live replay
--------------------------------------------------------------------
MTA's real-time (GTFS-realtime) feeds are present-tense only; there is no API
that returns historical train positions for a past date. The static GTFS feed we
download is also forward-looking (its calendar starts 2026-05-26), so it does not
literally contain 2026-05-20. May 20, 2026 was a **Wednesday**, and the subway
weekday timetable is highly stable, so we apply the feed's **Weekday** service
pattern to that date. Train motion is therefore the *scheduled* weekday service
(real routes, real stations, real timetable), reconstructed for the storm window.

Disruption flagging: a subway line is flagged "affected" in a given moment when one
of its stations is within ~450 m of a FloodNet sensor that is actively flooding at
that time (from storm_data.json). Per the chosen design, this only *flags* lines —
it does not alter their movement.
"""
import json
import math
import zipfile
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
GTFS = DATA / "gtfs_subway.zip"
STORM = ROOT / "storm_data.json"
OUT = ROOT / "storm_trains.json"

GTFS_URL = "http://web.mta.info/developers/data/nyct/subway/google_transit.zip"
SERVICE = "Weekday"
EDT_OFFSET = 4 * 3600          # America/New_York is UTC-4 on 2026-05-20 (EDT)
MAX_PTS = 12                   # cap points per trip
NEAR_M = 300                   # station<->sensor proximity for "affected"
ACTIVE_IN = 12                 # inches; only SEVERE flooding (track-level) flags a line


def haversine(a, b):
    (la1, lo1), (la2, lo2) = a, b
    R = 6371000
    p1, p2 = math.radians(la1), math.radians(la2)
    dp = math.radians(la2 - la1); dl = math.radians(lo2 - lo1)
    x = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(x))


def downsample(pts, cap):
    if len(pts) <= cap:
        return pts
    idx = sorted(set([0, len(pts)-1] + [round(i*(len(pts)-1)/(cap-1)) for i in range(cap)]))
    return [pts[i] for i in idx]


def main():
    if not GTFS.exists():
        import urllib.request
        print("downloading GTFS ...")
        urllib.request.urlretrieve(GTFS_URL, GTFS)

    storm = json.load(open(STORM, encoding="utf-8"))
    t0 = datetime.strptime(storm["meta"]["start_gmt"], "%Y-%m-%d %H:%M GMT").replace(tzinfo=timezone.utc)
    window = storm["meta"]["window_sec"]
    binw = storm["timeline"]["binw"]
    n_bins = len(storm["timeline"]["active"])

    z = zipfile.ZipFile(GTFS)
    trips = pd.read_csv(z.open("trips.txt"), dtype=str)
    routes = pd.read_csv(z.open("routes.txt"), dtype=str)
    stops = pd.read_csv(z.open("stops.txt"), dtype=str)
    st = pd.read_csv(z.open("stop_times.txt"), dtype=str,
                     usecols=["trip_id", "stop_id", "arrival_time", "stop_sequence"])

    stop_ll = {r.stop_id: (float(r.stop_lat), float(r.stop_lon))
               for r in stops.itertuples(index=False)}
    rcolor = {r.route_id: ("#" + (r.route_color or "888888")) for r in routes.itertuples(index=False)}
    rname = {r.route_id: r.route_short_name for r in routes.itertuples(index=False)}

    wk = trips[trips["service_id"] == SERVICE][["trip_id", "route_id"]]
    trip_route = dict(zip(wk["trip_id"], wk["route_id"]))
    wk_ids = set(wk["trip_id"])

    st = st[st["trip_id"].isin(wk_ids)].copy()

    def to_sec(t):
        h, m, s = t.split(":"); return int(h)*3600 + int(m)*60 + int(s)
    st["sec"] = st["arrival_time"].map(to_sec)
    st["seq"] = st["stop_sequence"].astype(int)
    st = st.sort_values(["trip_id", "seq"])

    # base offset (seconds from window start) for each service day, in UTC
    def base_for(local_midnight_utc):
        return (local_midnight_utc - t0).total_seconds()
    days = {
        "0519": base_for(datetime(2026, 5, 19, 4, 0, tzinfo=timezone.utc)),
        "0520": base_for(datetime(2026, 5, 20, 4, 0, tzinfo=timezone.utc)),
        "0521": base_for(datetime(2026, 5, 21, 4, 0, tzinfo=timezone.utc)),
    }

    # group stop_times per trip once
    trip_pts_raw = {}
    for tid, g in st.groupby("trip_id", sort=False):
        seq = [(row.sec, *stop_ll[row.stop_id]) for row in g.itertuples(index=False)
               if row.stop_id in stop_ll]
        if len(seq) >= 2:
            trip_pts_raw[tid] = seq

    # route -> set of stop coords (for proximity); collect from weekday trips
    route_stop_ll = {}
    for tid, seq in trip_pts_raw.items():
        rn = rname.get(trip_route.get(tid))
        s = route_stop_ll.setdefault(rn, set())
        for _, la, lo in seq:
            s.add((round(la, 4), round(lo, 4)))

    out_trips = []
    for tid, seq in trip_pts_raw.items():
        rid = trip_route.get(tid)
        rn, col = rname.get(rid, "?"), rcolor.get(rid, "#888")
        for base in days.values():
            pts = []
            for sec, la, lo in seq:
                toff = base + sec
                if -120 <= toff <= window + 120:
                    pts.append([round(toff), round(la, 5), round(lo, 5)])
            if len(pts) >= 2:
                pts = downsample(pts, MAX_PTS)
                out_trips.append({"r": rn, "c": col, "p": pts})

    # ---- disruption flagging ----
    # unique sensor coords + the bins in which each is actively flooding
    sensor_bins = {}   # (lat,lon) -> set(bin)
    for e in storm["events"]:
        key = (round(e["lat"], 5), round(e["lon"], 5))
        s = sensor_bins.setdefault(key, set())
        ser = e["s"]
        for b in range(n_bins):
            local = b*binw - e["t"]
            if local < 0 or local > e["dur"]:
                continue
            d = ser[-1][1] if local >= ser[-1][0] else ser[0][1]
            for i in range(len(ser)-1):
                if ser[i][0] <= local <= ser[i+1][0]:
                    t1, d1 = ser[i]; t2, d2 = ser[i+1]
                    d = d1 + (d2-d1)*((local-t1)/((t2-t1) or 1))
                    break
            if d > ACTIVE_IN:
                s.add(b)

    sensors = list(sensor_bins.keys())
    # route -> indices of nearby sensors
    route_near = {}
    for rn, coords in route_stop_ll.items():
        near = set()
        for si, sc in enumerate(sensors):
            for c in coords:
                if haversine(c, sc) <= NEAR_M:
                    near.add(si); break
        if near:
            route_near[rn] = near

    disrupted = []
    for b in range(n_bins):
        active_sensors = {si for si, sc in enumerate(sensors) if b in sensor_bins[sc]}
        hit = sorted({rn for rn, near in route_near.items() if near & active_sensors})
        disrupted.append(hit)

    ever = sorted({rn for d in disrupted for rn in d})
    route_colors = {rn: rcolor[rid] for rid, rn in rname.items()}

    out = {
        "meta": {
            "window_sec": window, "binw": binw, "n_bins": n_bins,
            "n_trips": len(out_trips),
            "service_basis": "Weekday timetable applied to 2026-05-20 (Wed); "
                             "GTFS calendar does not archive the past date.",
            "disrupted_lines": ever,
        },
        "route_colors": route_colors,
        "trips": out_trips,
        "disrupted": disrupted,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))

    import os
    print(f"Wrote {OUT.name} ({os.path.getsize(OUT)/1e6:.2f} MB)")
    print(f"trips animated: {len(out_trips):,}")
    print(f"lines ever flagged as storm-affected: {', '.join(ever) or '(none)'}")
    peak_b = max(range(n_bins), key=lambda b: len(disrupted[b]))
    print(f"peak disrupted lines: {len(disrupted[peak_b])} at bin {peak_b} "
          f"(~{(t0).strftime('%H:%M')} + {round(peak_b*binw/3600,1)}h GMT): "
          f"{', '.join(disrupted[peak_b])}")


if __name__ == "__main__":
    main()
