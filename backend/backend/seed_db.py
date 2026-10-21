"""PART 1: builds restaurants.db from your CSV files. Run once: python seed_db.py"""
import re, sqlite3, sys
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
RAW = HERE.parent / "data" / "raw"
DB = HERE / "restaurants.db"

# Words removed first so they do not trigger false allergen matches
NOT_ALLERGENS = ["coconut milk", "eggplant", "rice flour", "gram flour", "chickpea flour",
                 "corn flour", "cornflour", "besan", "lentil flour", "ragi flour",
                 "bajra flour", "jowar flour", "urad dal flour", "tapioca flour", "potato flour"]
ALLERGENS = {
    "dairy": ["milk", "yogurt", "curd", "ghee", "paneer", "butter", "cream", "khoa", "cheese", "malai", "condensed",
              "chenna", "chhena", "dahi", "yoghurt", "mawa", "low fat"],
    "gluten": ["maida", "wheat", "atta", "semolina", "rava", "sooji", "suji", "bread", "vermicelli", "flour", "dalia",
               "pav", "bhatura", "dinner roll", "dough", "sev", "sevai", "falooda"],
    "peanuts": ["peanut"],
    "tree nuts": ["cashew", "almond", "pistachio", "walnut", "hazelnut", "badam", "nuts"],
    "eggs": ["egg"],
    "seafood": ["fish", "prawn", "shrimp", "crab", "lobster", "bombay duck", "seafood"],
    "soy": ["soy"],
    "sesame": ["sesame", "gingelly"],
}
PROTEIN = ["chicken", "mutton", "lamb", "pork", "fish", "prawn", "crab", "egg", "paneer", "dal",
           "lentil", "chickpea", "chana", "rajma", "beans", "moong", "gram", "tofu", "soya", "urad"]
COMFORT = ["rice", "potato", "ghee", "butter", "cream", "milk", "bread", "curd", "yogurt", "lentil"]

def clean(text):
    return str(text).lower().replace("eggplant", "").replace("coconut milk", "")

def allergens_of(ingredients):
    t = str(ingredients).lower()
    for w in NOT_ALLERGENS:
        t = t.replace(w, "")
    return ";".join(a for a, words in ALLERGENS.items() if any(w in t for w in words))

def build_dishes(df, cuisine=None):
    out = pd.DataFrame()
    out["name"] = df["name"]
    out["cuisine"] = cuisine if cuisine else df["cuisine"]
    out["ingredients"] = df["ingredients"]
    out["diet"] = df["diet"] if "diet" in df else ""
    out["course"] = df["course"] if "course" in df else ""
    out["state"] = df["state"].replace("-1", "") if "state" in df else ""
    out["allergens"] = df["ingredients"].apply(allergens_of)
    ing = df["ingredients"].apply(clean)
    out["is_protein"] = ing.apply(lambda t: int(any(w in t for w in PROTEIN)))
    out["is_comfort"] = [int(c == "dessert" or any(w in t for w in COMFORT))
                         for c, t in zip(out["course"], ing)]
    return out

def to_number(series):
    return pd.to_numeric(series.astype(str).str.replace(",", "").str.extract(r"(\d+)")[0], errors="coerce")

def attach_coords(df):
    """Fill lat/lng from the CSV itself, then from data/area_coords.csv (made by geocode_areas.py)."""
    for target, options in {"lat": ["lat", "latitude"], "lng": ["lng", "lon", "longitude"]}.items():
        found = next((c for c in options if c in df.columns), None)
        df[target] = pd.to_numeric(df[found], errors="coerce") if found else float("nan")
    cache = RAW.parent / "area_coords.csv"
    if cache.exists():
        known = pd.read_csv(cache)
        make_key = lambda a, l: a.fillna("").astype(str).str.lower().str.strip() + "|" + l.fillna("").astype(str).str.lower().str.strip()
        lookup = known.assign(key=make_key(known["area"], known["location"])).drop_duplicates("key").set_index("key")
        keys = make_key(df["area"], df["location"])
        for c in ["lat", "lng"]:
            df[c] = df[c].fillna(keys.map(lookup[c]))
    print(f"Restaurants with coordinates: {int(df['lat'].notna().sum())} of {len(df)}")
    return df

def main():
    con = sqlite3.connect(DB)

    dishes = build_dishes(pd.read_csv(RAW / "indian_food.csv"), cuisine="Indian")
    extra = RAW.parent / "extra_dishes.csv"   # optional: name,cuisine,ingredients,diet,course
    if extra.exists():
        dishes = pd.concat([dishes, build_dishes(pd.read_csv(extra))], ignore_index=True)
    dishes.to_sql("dishes", con, if_exists="replace", index=False)
    print(f"Loaded {len(dishes)} dishes")

    swiggy = RAW / "swiggy.csv"
    cols = ["restaurant_name", "cuisine", "rating", "number_of_ratings", "price",
            "area", "location", "pure_veg", "offer_name", "lat", "lng"]
    if not swiggy.exists():
        print("swiggy.csv not found in data/raw - dishes work, restaurants will be empty.")
        pd.DataFrame(columns=cols).to_sql("restaurants", con, if_exists="replace", index=False)
    else:
        df = pd.read_csv(swiggy)
        df.columns = [re.sub(r"\W+", "_", c.strip().lower()) for c in df.columns]
        print("Swiggy columns:", list(df.columns))
        missing = [c for c in ["restaurant_name", "cuisine", "rating", "average_price", "area"] if c not in df.columns]
        if missing:
            sys.exit(f"Missing columns {missing}. Edit seed_db.py to match the names printed above.")
        for c in ["number_of_ratings", "location", "pure_veg", "offer_name"]:
            if c not in df.columns:
                df[c] = None
        df["price"] = to_number(df["average_price"])
        df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
        df["number_of_ratings"] = to_number(df["number_of_ratings"])
        df = df.dropna(subset=["price", "cuisine"])
        df = attach_coords(df)
        df[cols].to_sql("restaurants", con, if_exists="replace", index=False)
        print(f"Loaded {len(df)} restaurants")
    con.commit()
    con.close()

if __name__ == "__main__":
    main()
