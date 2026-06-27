"""
Build storm_buses.json: animate NYC buses (all five boroughs + MTA Bus Company)
across the May 20, 2026 storm window, and flag bus routes disrupted by flooding.

Same honesty caveat as the subway (see build_storm_trains.py): MTA's real-time bus
feed (Bus Time / GTFS-realtime) is present-tense only and cannot replay a past date,
and the static bus GTFS is forward-looking (calendars start ~late June 2026). May 20,
2026 was a Wednesday, so we apply each feed's **weekday** service pattern to that date.
Motion is therefore the *scheduled* weekday bus service (real routes, stops, timetable),
not a recording of what actually ran.

Because all-NYC bus service is enormous, trips are thinned to a target count to keep the
file and the in-browser animation manageable (documented in the README).
"""
import json
import math
import random
import zipfile
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STORM = ROOT / "storm_data.json"
OUT = ROOT / "storm_buses.json"

FEEDS = {"m": "gtfs_bus_m.zip", "b": "gtfs_bus_b.zip", "bx": "gtfs_bus_bx.zip",
         "q": "gtfs_bus_q.zip", "si": "gtfs_bus_si.zip", "busco": "gtfs_bus_busco.zip"}
FEED_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_{}.zip"

MAX_PTS = 8
NEAR_M = 300
DISRUPT_IN = 12
TARGET_TRIPS = 11000          # thin to ~this many buses citywide
random.seed(7)


def haversine(a, b):
    R = 6371000
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = math.radians(b[0]-a[0]); dl = math.radians(b[1]-a[1])
    x = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(x))


def downsample(pts, cap):
    if len(pts) <= cap:
        return pts
    idx = sorted(set([0, len(pts)-1] + [round(i*(len(pts)-1)/(cap-1)) for i in range(cap)]))
    return [pts[i] for i in idx]


def to_sec(t):
    h, m, s = t.split(":"); return int(h)*3600 + int(m)*60 + int(s)


def weekday_services(z):
    """Service IDs running on a Wednesday, de-duplicated across school-day variants."""
    try:
        cal = pd.read_csv(z.open("calendar.txt"), dtype=str)
        wed = cal[cal["wednesday"] == "1"]["service_id"].tolist()
    except KeyError:
        wed = []
    # dedupe: collapse "-SDon"/"-SDoff" school variants, prefer school-day-on (May = school)
    import re
    groups = {}
    for sid in wed:
        key = re.sub(r"-(SDon|SDoff)$", "", sid)
        cur = groups.get(key)
        if cur is None or (sid.endswith("SDon") and not cur.endswith("SDon")):
            groups[key] = sid
    return set(groups.values())


def main():
    for code, fn in FEEDS.items():
        p = DATA / fn
        if not p.exists():
            import urllib.request
            print("downloading", fn)
            urllib.request.urlretrieve(FEED_URL.format(code), p)

    storm = json.load(open(STORM, encoding="utf-8"))
    t0 = datetime.strptime(storm["meta"]["start_gmt"], "%Y-%m-%d %H:%M GMT").replace(tzinfo=timezone.utc)
    window = storm["meta"]["window_sec"]
    binw = storm["timeline"]["binw"]
    n_bins = len(storm["timeline"]["active"])

    def base_for(day):
        return (datetime(2026, 5, day, 4, 0, tzinfo=timezone.utc) - t0).total_seconds()
    day_bases = [base_for(19), base_for(20), base_for(21)]

    all_trips = []                       # {"r":short,"p":[[t,lat,lon],...]}
    route_stop_ll = {}                   # route_short -> set of (lat,lon) rounded
    for code, fn in FEEDS.items():
        z = zipfile.ZipFile(DATA / fn)
        trips = pd.read_csv(z.open("trips.txt"), dtype=str)
        routes = pd.read_csv(z.open("routes.txt"), dtype=str)
        stops = pd.read_csv(z.open("stops.txt"), dtype=str)
        rname = {r.route_id: r.route_short_name for r in routes.itertuples(index=False)}
        stop_ll = {r.stop_id: (float(r.stop_lat), float(r.stop_lon))
                   for r in stops.itertuples(index=False)}

        svc = weekday_services(z)
        wk = trips[trips["service_id"].isin(svc)][["trip_id", "route_id"]]
        trip_route = dict(zip(wk["trip_id"], wk["route_id"]))
        wk_ids = set(wk["trip_id"])

        st = pd.read_csv(z.open("stop_times.txt"), dtype=str,
                         usecols=["trip_id", "stop_id", "arrival_time", "stop_sequence"])
        st = st[st["trip_id"].isin(wk_ids)].copy()
        st["sec"] = st["arrival_time"].map(to_sec)
        st["seq"] = st["stop_sequence"].astype(int)
        st = st.sort_values(["trip_id", "seq"])

        feed_trips = []
        for tid, g in st.groupby("trip_id", sort=False):
            seq = [(row.sec, *stop_ll[row.stop_id]) for row in g.itertuples(index=False)
                   if row.stop_id in stop_ll]
            if len(seq) < 2:
                continue
            rn = rname.get(trip_route.get(tid), "?")
            for base in day_bases:
                pts = [[round(base+sec), round(la, 5), round(lo, 5)]
                       for sec, la, lo in seq if -120 <= base+sec <= window+120]
                if len(pts) >= 2:
                    feed_trips.append({"r": rn, "p": downsample(pts, MAX_PTS)})
        all_trips += feed_trips
        print(f"  {code:6} weekday svc={len(svc):2}  trips+={len(feed_trips):,}")

    # thin to target
    if len(all_trips) > TARGET_TRIPS:
        all_trips = random.sample(all_trips, TARGET_TRIPS)
    # route -> stop coords (from KEPT trips, so flags match what's drawn)
    for t in all_trips:
        s = route_stop_ll.setdefault(t["r"], set())
        for _, la, lo in t["p"]:
            s.add((round(la, 4), round(lo, 4)))

    # ---- disruption flags: bus route near severe flooding ----
    sensor_bins = {}
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
                    d = d1 + (d2-d1)*((local-t1)/((t2-t1) or 1)); break
            if d > DISRUPT_IN:
                s.add(b)
    sensors = list(sensor_bins.keys())
    route_near = {}
    for rn, coords in route_stop_ll.items():
        near = set()
        for si, sc in enumerate(sensors):
            if any(haversine(c, sc) <= NEAR_M for c in coords):
                near.add(si)
        if near:
            route_near[rn] = near
    disrupted = []
    for b in range(n_bins):
        act = {si for si, sc in enumerate(sensors) if b in sensor_bins[sc]}
        disrupted.append(sorted({rn for rn, nr in route_near.items() if nr & act}))
    ever = sorted({rn for d in disrupted for rn in d})

    out = {
        "meta": {"window_sec": window, "binw": binw, "n_bins": n_bins,
                 "n_trips": len(all_trips), "target_trips": TARGET_TRIPS,
                 "service_basis": "Weekday timetable applied to 2026-05-20 (Wed); thinned to ~%d trips." % TARGET_TRIPS,
                 "disrupted_route_count": len(ever)},
        "trips": all_trips,
        "disrupted": disrupted,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))

    import os
    print(f"Wrote {OUT.name} ({os.path.getsize(OUT)/1e6:.2f} MB)")
    print(f"bus trips animated: {len(all_trips):,}")
    print(f"bus routes ever flagged: {len(ever)}")
    peak_b = max(range(n_bins), key=lambda b: len(disrupted[b]))
    print(f"peak disrupted bus routes: {len(disrupted[peak_b])} (~{round(peak_b*binw/3600,1)}h into window)")


if __name__ == "__main__":
    main()
