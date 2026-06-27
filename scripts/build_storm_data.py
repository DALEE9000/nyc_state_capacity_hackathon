"""
Build storm_data.json: the May 20, 2026 NYC street-flooding event, as a
time-animated dataset. For every FloodNet flood event that day we emit the
sensor location and a downsampled depth-vs-time series (seconds from the storm
window start), so the frontend can replay how flooding rose and drained across
the city. Also precomputes a timeline curve (active sensors + max depth per bin).
"""
import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent     # repo root
DATA = ROOT / "data"                               # raw CSVs (gitignored)
EVT = str(DATA / "FloodNet__Street_Flooding_Events_Measured_by_FloodNet_Sensors_20260624.csv")
SENS = str(DATA / "FloodNet__Sensor_Deployment_Metadata_20260624.csv")
OUT = str(ROOT / "storm_data.json")                # fetched by index.html
DAY = "2026-05-20"
MAX_PTS = 48           # cap series length per event
N_BINS = 140           # timeline curve resolution


def parse_arr(s):
    try:
        return json.loads(s)
    except Exception:
        return []


def downsample(ts, ds, cap):
    """Keep first, last, the peak, and an even stride in between."""
    pairs = [(float(t), float(d)) for t, d in zip(ts, ds)
             if t is not None and d is not None]
    if not pairs:
        return []
    if len(pairs) <= cap:
        keep = pairs
    else:
        stride = len(pairs) / cap
        idx = sorted(set([0, len(pairs) - 1] +
                         [int(i * stride) for i in range(cap)] +
                         [max(range(len(pairs)), key=lambda i: pairs[i][1])]))
        keep = [pairs[i] for i in idx]
    return [[round(t), round(d, 2)] for t, d in keep]


def main():
    sm = pd.read_csv(SENS, dtype=str).dropna(subset=["Latitude", "Longitude"])
    loc = {r["Sensor ID"]: (round(float(r["Latitude"]), 5),
                            round(float(r["Longitude"]), 5),
                            r.get("Borough", ""))
           for _, r in sm.iterrows()}

    ev = pd.read_csv(EVT, dtype=str)
    ev["start"] = pd.to_datetime(ev["Flood Start Datetime (GMT)"], errors="coerce")
    ev["end"] = pd.to_datetime(ev["Flood End Datetime (GMT)"], errors="coerce")
    day = ev[(ev["start"].dt.strftime("%Y-%m-%d") == DAY) |
             (ev["end"].dt.strftime("%Y-%m-%d") == DAY)].copy()
    day = day[day["Sensor ID"].isin(loc)]

    t0 = day["start"].min()
    t_end = day["end"].max()
    window = (t_end - t0).total_seconds()

    events = []
    for _, r in day.iterrows():
        lat, lon, boro = loc[r["Sensor ID"]]
        ts = parse_arr(r["Time Series Depth Timestamps (seconds)"])
        ds = parse_arr(r["Time Series Depth Values (inches)"])
        series = downsample(ts, ds, MAX_PTS)
        if not series:
            continue
        startSec = (r["start"] - t0).total_seconds()
        peak = max(d for _, d in series)
        events.append({
            "name": r["Sensor Name"], "boro": boro,
            "lat": lat, "lon": lon,
            "t": round(startSec),                 # storm-clock offset of flood start
            "dur": round(series[-1][0]),          # local duration (s)
            "peak": round(peak, 1),
            "s": series,                          # [[localSec, depthIn], ...]
        })

    # Timeline curve: per bin, # actively flooding sensors and max depth
    binw = window / N_BINS
    active = [0] * N_BINS
    maxd = [0.0] * N_BINS
    for e in events:
        for b in range(N_BINS):
            clk = b * binw
            local = clk - e["t"]
            if local < 0 or local > e["dur"]:
                continue
            # interpolate depth at local
            s = e["s"]
            d = 0.0
            for i in range(len(s) - 1):
                if s[i][0] <= local <= s[i + 1][0]:
                    t1, d1 = s[i]; t2, d2 = s[i + 1]
                    d = d1 + (d2 - d1) * ((local - t1) / (t2 - t1 or 1))
                    break
            else:
                d = s[-1][1] if local >= s[-1][0] else s[0][1]
            if d > 0.3:
                active[b] += 1
                maxd[b] = max(maxd[b], d)

    peak_bin = max(range(N_BINS), key=lambda b: active[b])
    out = {
        "meta": {
            "date": DAY,
            "start_gmt": t0.strftime("%Y-%m-%d %H:%M GMT"),
            "end_gmt": t_end.strftime("%Y-%m-%d %H:%M GMT"),
            "window_sec": round(window),
            "n_events": len(events),
            "n_sensors": day["Sensor ID"].nunique(),
            "peak_depth_in": round(max(e["peak"] for e in events), 1),
            "peak_active": max(active),
            "peak_time_gmt": (t0 + pd.Timedelta(seconds=peak_bin * binw)).strftime("%H:%M GMT"),
        },
        "events": events,
        "timeline": {"binw": round(binw), "active": active,
                     "maxd": [round(x, 1) for x in maxd]},
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))

    import os
    print(f"Wrote {OUT} ({os.path.getsize(OUT)/1e3:.0f} KB)")
    print("Window:", out["meta"]["start_gmt"], "->", out["meta"]["end_gmt"],
          f"({window/3600:.1f} h)")
    print("Events:", out["meta"]["n_events"], "| sensors:", out["meta"]["n_sensors"])
    print("Peak depth:", out["meta"]["peak_depth_in"], "in | peak active:",
          out["meta"]["peak_active"], "sensors @", out["meta"]["peak_time_gmt"])


if __name__ == "__main__":
    main()
