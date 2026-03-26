"""
Multi-year SSE capture — all 5 locations, 20 years each.

Usage:
    pip install sseclient-py
    python capture_multiyear_20yr.py

Backend must be running at http://localhost:8000
"""

import json
import time
import requests
import sseclient

BASE = "http://localhost:8000"

SCENARIOS = [
    # {
    #     "id": "QM1", "loc": "Quincy",
    #     "t1": "25 MW GPO near Quincy CA at 39.94N 120.95W. Project costs over 20 years.",
    #     "t2": "Go with your first recommendation.",
    #     "n_years": 20, "frredss_y1": 40.34,
    # },
    {
        "id": "QM2", "loc": "Redding",
        "t1": "20 MW GPO near Redding CA at 40.59N 122.39W. 20-year cost projection.",
        "t2": "Use option 1.",
        "n_years": 20, "frredss_y1": 42.33,
    },
    {
        "id": "QM3", "loc": "Ukiah",
        "t1": "15 MW GPO near Ukiah CA at 39.15N 123.21W. 20-year cost projection.",
        "t2": "Go with option 1.",
        "n_years": 20, "frredss_y1": 43.41,
    },
    {
        "id": "QM4", "loc": "Fresno foothills",
        "t1": "30 MW GPO at 37.18N 119.75W. 20-year projection, cost only.",
        "t2": "Use your first recommendation.",
        "n_years": 20, "frredss_y1": 53.26,
    },
    {
        "id": "QM5", "loc": "Mt Shasta",
        "t1": "20 MW GPO near Mount Shasta CA at 41.31N 122.19W. 20-year cost projection.",
        "t2": "Go with your first recommendation.",
        "n_years": 20, "frredss_y1": 38.82,
    },
]


def chat(message, session_id, timeout=600):
    t0 = time.time()
    resp = requests.post(
        f"{BASE}/chat",
        json={"message": message, "session_id": session_id},
        timeout=timeout,
    )
    elapsed = time.time() - t0
    if resp.status_code in (429, 529):
        print(f"  [Rate limited] waiting 30s...")
        time.sleep(30)
        resp = requests.post(
            f"{BASE}/chat",
            json={"message": message, "session_id": session_id},
            timeout=timeout,
        )
        elapsed = time.time() - t0
    data = resp.json()
    return {"response": data.get("response", ""), "elapsed": round(elapsed, 1)}


def stream(session_id, timeout=400):
    url = f"{BASE}/multi-year/stream?session_id={session_id}"
    t0 = time.time()
    years = []
    try:
        resp = requests.post(url, stream=True, timeout=timeout)
        client = sseclient.SSEClient(resp)
        for event in client.events():
            if not event.data:
                continue
            try:
                d = json.loads(event.data)
            except:
                continue
            if d.get("done"):
                break
            if d.get("error"):
                print(f"  SSE error: {d['error']}")
                break
            if d.get("year"):
                c = d.get("avg_delivered_cost", 0)
                r = d.get("effective_radius_km")
                n = d.get("n_selected")
                print(f"    Y{d['year']}: ${c:.2f}/GT  r={r}km  n={n}")
                years.append(d)
    except Exception as e:
        print(f"  Stream error: {e}")
    return years, round(time.time() - t0, 1)


if __name__ == "__main__":
    results = {}

    for s in SCENARIOS:
        sid = f"{s['id']}_{int(time.time())}"
        print(f"\n{'='*60}")
        print(f"[{s['id']}] {s['loc']} — 20-year GPO projection")
        print(f"{'='*60}")

        print(f"T1: {s['t1'][:70]}")
        r1 = chat(s["t1"], sid)
        print(f"  {r1['elapsed']:.1f}s")
        time.sleep(5)

        print(f"T2: {s['t2']}")
        r2 = chat(s["t2"], sid)
        print(f"  {r2['elapsed']:.1f}s")
        print(f"  {r2['response'][:150].replace(chr(10),' ')}")

        wait = max(20, r2["elapsed"] + 15)  
        print(f"  Waiting {wait:.0f}s for pipeline to complete...")
        time.sleep(wait)

        years, stream_t = stream(sid)
        costs = [y.get("avg_delivered_cost", 0) for y in years]
        print(f"  Captured {len(years)} years in {stream_t:.1f}s")
        if costs:
            print(f"  Y1=${costs[0]:.2f}  Y{len(costs)}=${costs[-1]:.2f}  "
                  f"esc=+{(costs[-1]-costs[0])/costs[0]*100:.1f}%")
            if any(c == 0 for c in costs):
                ex_yr = next(i+1 for i,c in enumerate(costs) if c == 0)
                print(f"  *** SUPPLY EXHAUSTED Year {ex_yr} ***")

        results[s["id"]] = {
            "location": s["loc"], "n_years": 20,
            "t1_elapsed": r1["elapsed"], "t2_elapsed": r2["elapsed"],
            "t2_response": r2["response"][:300],
            "stream_elapsed": stream_t, "years": years,
            "frredss_y1": s["frredss_y1"],
        }

        print(f"\nWaiting 15s before next scenario...")
        time.sleep(15)

    with open("multiyear_20yr_results.json", "w") as f:
        json.dump(results, f, indent=2)

    print("\n✓ Saved to multiyear_20yr_results.json — upload to update the figure.")