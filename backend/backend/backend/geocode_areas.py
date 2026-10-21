"""OPTIONAL. Run on YOUR computer, once: python geocode_areas.py --contains Guwahati --max 300
Looks up GPS coordinates for restaurant areas (1 per second, free OpenStreetMap service)
and saves them in data/area_coords.csv. Safe to stop and run again: it skips finished areas."""
import argparse, csv, json, re, time, urllib.parse, urllib.request
from pathlib import Path
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
OUT = DATA / "area_coords.csv"

def lookup(query_text):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": query_text, "format": "jsonv2", "limit": 1, "countrycodes": "in"})
    req = urllib.request.Request(url, headers={"User-Agent": "banana-bites-student-project"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    return (float(data[0]["lat"]), float(data[0]["lon"])) if data else ("", "")

def key(area, location):
    return f"{str(area).lower().strip()}|{str(location).lower().strip()}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contains", default="", help="only areas or cities containing this text")
    ap.add_argument("--max", type=int, default=300, help="how many areas to look up this run")
    a = ap.parse_args()

    df = pd.read_csv(DATA / "raw" / "swiggy.csv")
    df.columns = [re.sub(r"\W+", "_", c.strip().lower()) for c in df.columns]
    if "location" not in df:
        df["location"] = ""
    pairs = df[["area", "location"]].fillna("").astype(str).drop_duplicates()
    if a.contains:
        hit = (pairs["area"].str.contains(a.contains, case=False, regex=False) |
               pairs["location"].str.contains(a.contains, case=False, regex=False))
        pairs = pairs[hit]

    done = set()
    if OUT.exists():
        old = pd.read_csv(OUT).fillna("")
        done = {key(x, y) for x, y in zip(old["area"], old["location"])}
    todo = [(x, y) for x, y in zip(pairs["area"], pairs["location"]) if key(x, y) not in done][:a.max]
    print(f"{len(todo)} areas to look up (about {int(len(todo) * 1.1 / 60) + 1} minutes)")

    new_file = not OUT.exists()
    with open(OUT, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["area", "location", "lat", "lng"])
        for n, (area, location) in enumerate(todo, 1):
            try:
                lat, lng = lookup(", ".join(x for x in (area, location, "India") if x))
            except Exception as e:
                print("skipped", area, e)
                continue
            w.writerow([area, location, lat, lng])
            f.flush()
            print(n, area, location, lat, lng)
            time.sleep(1.1)

if __name__ == "__main__":
    main()
