
"""PART 1: Build restaurants.db from your CSV files.
Run from the backend folder: python seed_db.py
"""

import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd


# --------------------------------------------------
# PATHS
# --------------------------------------------------

HERE = Path(__file__).resolve().parent
RAW = HERE.parent / "data" / "raw"
DB = HERE / "restaurants.db"


# --------------------------------------------------
# ALLERGEN DETECTION
# --------------------------------------------------

# Remove these phrases first to reduce false allergen matches.
NOT_ALLERGENS = [
    "coconut milk",
    "eggplant",
    "rice flour",
    "gram flour",
    "chickpea flour",
    "corn flour",
    "cornflour",
    "besan",
    "lentil flour",
    "ragi flour",
    "bajra flour",
    "jowar flour",
    "urad dal flour",
    "tapioca flour",
    "potato flour",
]

ALLERGENS = {
    "dairy": [
        "milk", "yogurt", "curd", "ghee", "paneer", "butter",
        "cream", "khoa", "cheese", "malai", "condensed",
        "chenna", "chhena", "dahi", "yoghurt", "mawa", "low fat",
    ],
    "gluten": [
        "maida", "wheat", "atta", "semolina", "rava", "sooji",
        "suji", "bread", "vermicelli", "flour", "dalia", "pav",
        "bhatura", "dinner roll", "dough", "sev", "sevai", "falooda",
    ],
    "peanuts": ["peanut"],
    "tree nuts": [
        "cashew", "almond", "pistachio", "walnut",
        "hazelnut", "badam", "nuts",
    ],
    "eggs": ["egg"],
    "seafood": [
        "fish", "prawn", "shrimp", "crab", "lobster",
        "bombay duck", "seafood",
    ],
    "soy": ["soy"],
    "sesame": ["sesame", "gingelly"],
}

PROTEIN = [
    "chicken", "mutton", "lamb", "pork", "fish", "prawn",
    "crab", "egg", "paneer", "dal", "lentil", "chickpea",
    "chana", "rajma", "beans", "moong", "gram", "tofu",
    "soya", "urad",
]

COMFORT = [
    "rice", "potato", "ghee", "butter", "cream",
    "milk", "bread", "curd", "yogurt", "lentil",
]


# --------------------------------------------------
# GENERAL HELPERS
# --------------------------------------------------

def clean(text):
    """Normalize ingredient text for category matching."""
    return (
        str(text)
        .lower()
        .replace("eggplant", "")
        .replace("coconut milk", "")
    )


def allergens_of(ingredients):
    """Return a semicolon-separated list of possible allergens."""
    text = str(ingredients).lower()

    for word in NOT_ALLERGENS:
        text = text.replace(word, "")

    return ";".join(
        allergen
        for allergen, words in ALLERGENS.items()
        if any(word in text for word in words)
    )


def build_dishes(df, cuisine=None):
    """Prepare the dish dataset used by the recommendation API."""
    out = pd.DataFrame()

    out["name"] = df["name"]
    out["cuisine"] = cuisine if cuisine else df["cuisine"]
    out["ingredients"] = df["ingredients"]

    out["diet"] = df["diet"] if "diet" in df.columns else ""
    out["course"] = df["course"] if "course" in df.columns else ""

    out["state"] = (
        df["state"].replace("-1", "")
        if "state" in df.columns
        else ""
    )

    out["allergens"] = df["ingredients"].apply(allergens_of)

    ingredients = df["ingredients"].apply(clean)

    out["is_protein"] = ingredients.apply(
        lambda text: int(any(word in text for word in PROTEIN))
    )

    out["is_comfort"] = [
        int(course == "dessert" or any(word in text for word in COMFORT))
        for course, text in zip(out["course"], ingredients)
    ]

    return out


def to_number(series):
    """Extract a numeric value from prices or rating counts."""
    return pd.to_numeric(
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.extract(r"(\d+)")[0],
        errors="coerce",
    )


# --------------------------------------------------
# RESTAURANT COORDINATES
# --------------------------------------------------

def attach_coords(df):
    """Read coordinates from the CSV or the area coordinate cache."""

    for target, options in {
        "lat": ["lat", "latitude"],
        "lng": ["lng", "lon", "longitude"],
    }.items():

        found = next(
            (column for column in options if column in df.columns),
            None,
        )

        if found:
            df[target] = pd.to_numeric(df[found], errors="coerce")
        else:
            df[target] = float("nan")

    # Optional cache created by backend/geocode_areas.py
    cache = RAW.parent / "area_coords.csv"

    if cache.exists():
        known = pd.read_csv(cache)

        required_cache_columns = {"area", "location", "lat", "lng"}

        if required_cache_columns.issubset(known.columns):

            def make_key(area, location):
                return (
                    area.fillna("").astype(str).str.lower().str.strip()
                    + "|"
                    + location.fillna("").astype(str).str.lower().str.strip()
                )

            lookup = (
                known.assign(
                    key=make_key(known["area"], known["location"])
                )
                .drop_duplicates("key")
                .set_index("key")
            )

            keys = make_key(df["area"], df["location"])

            for column in ["lat", "lng"]:
                df[column] = df[column].fillna(keys.map(lookup[column]))

        else:
            print(
                "Warning: area_coords.csv is missing required columns. "
                "Skipping coordinate cache."
            )

    print(
        f"Restaurants with coordinates: "
        f"{int(df['lat'].notna().sum())} of {len(df)}"
    )

    return df


# --------------------------------------------------
# MAIN DATABASE SEEDING
# --------------------------------------------------

def main():
    # Make sure the input directory exists.
    if not RAW.exists():
        sys.exit(
            f"Data folder not found: {RAW}\n"
            "Make sure your CSV files are inside data/raw/."
        )

    # Create or open the restaurant database.
    con = sqlite3.connect(DB)

    try:
        # ------------------------------------------
        # 1. LOAD DISHES
        # ------------------------------------------

        indian_food = RAW / "indian_food.csv"

        if not indian_food.exists():
            raise FileNotFoundError(
                f"Required file not found: {indian_food}"
            )

        dishes = build_dishes(
            pd.read_csv(indian_food),
            cuisine="Indian",
        )

        # Optional additional dishes CSV.
        extra = RAW.parent / "extra_dishes.csv"

        if extra.exists():
            extra_df = build_dishes(pd.read_csv(extra))

            dishes = pd.concat(
                [dishes, extra_df],
                ignore_index=True,
            )

        dishes.to_sql(
            "dishes",
            con,
            if_exists="replace",
            index=False,
        )

        print(f"Loaded {len(dishes)} dishes")

        # ------------------------------------------
        # 2. LOAD RESTAURANTS
        # ------------------------------------------

        swiggy = RAW / "swiggy.csv"

        restaurant_columns = [
            "restaurant_name",
            "cuisine",
            "rating",
            "number_of_ratings",
            "price",
            "area",
            "location",
            "pure_veg",
            "offer_name",
            "lat",
            "lng",
        ]

        if not swiggy.exists():
            print(
                "swiggy.csv not found in data/raw - "
                "dishes will load, but restaurants will be empty."
            )

            pd.DataFrame(
                columns=restaurant_columns
            ).to_sql(
                "restaurants",
                con,
                if_exists="replace",
                index=False,
            )

        else:
            df = pd.read_csv(swiggy)

            # Standardize column names, for example:
            # "Avg ratings" -> "avg_ratings"
            # "Food type"   -> "food_type"
            df.columns = [
                re.sub(r"\W+", "_", column.strip().lower())
                for column in df.columns
            ]

            print("Original normalized Swiggy columns:", list(df.columns))

            # --------------------------------------
            # IMPORTANT: MAP SWIGGY CSV COLUMN NAMES
            # --------------------------------------

            # Your CSV contains:
            # Restaurant, Food type, Avg ratings,
            # Price, Total ratings, Area, City
            #
            # The backend expects different column names.
            rename_columns = {
                "restaurant": "restaurant_name",
                "food_type": "cuisine",
                "avg_ratings": "rating",
                "price": "average_price",
                "total_ratings": "number_of_ratings",
                "city": "location",
            }

            df = df.rename(columns=rename_columns)

            print("Mapped Swiggy columns:", list(df.columns))

            # --------------------------------------
            # CHECK REQUIRED COLUMNS
            # --------------------------------------

            required_columns = [
                "restaurant_name",
                "cuisine",
                "rating",
                "average_price",
                "area",
            ]

            missing = [
                column
                for column in required_columns
                if column not in df.columns
            ]

            if missing:
                raise ValueError(
                    f"Missing required Swiggy columns: {missing}. "
                    f"Available columns: {list(df.columns)}"
                )

            # --------------------------------------
            # FILL OPTIONAL COLUMNS
            # --------------------------------------

            for column in [
                "number_of_ratings",
                "location",
                "pure_veg",
                "offer_name",
            ]:
                if column not in df.columns:
                    df[column] = None

            # --------------------------------------
            # CLEAN NUMERIC VALUES
            # --------------------------------------

            df["price"] = to_number(df["average_price"])

            df["rating"] = pd.to_numeric(
                df["rating"],
                errors="coerce",
            )

            df["number_of_ratings"] = to_number(
                df["number_of_ratings"]
            )

            # Remove rows without usable prices or cuisine.
            df = df.dropna(subset=["price", "cuisine"])

            # Remove rows without restaurant names.
            df = df.dropna(subset=["restaurant_name"])

            df["restaurant_name"] = (
                df["restaurant_name"].astype(str).str.strip()
            )

            df["cuisine"] = df["cuisine"].astype(str).str.strip()
            df["area"] = df["area"].fillna("").astype(str).str.strip()
            df["location"] = (
                df["location"].fillna("").astype(str).str.strip()
            )

            df = df[
                (df["restaurant_name"] != "")
                & (df["cuisine"] != "")
            ]

            # --------------------------------------
            # ADD GPS COORDINATES
            # --------------------------------------

            df = attach_coords(df)

            # --------------------------------------
            # SAVE RESTAURANTS TABLE
            # --------------------------------------

            # Ensure every expected database column exists.
            for column in restaurant_columns:
                if column not in df.columns:
                    df[column] = None

            df[restaurant_columns].to_sql(
                "restaurants",
                con,
                if_exists="replace",
                index=False,
            )

            print(f"Loaded {len(df)} restaurants")

        # Save all database changes.
        con.commit()

        print(f"Database successfully created/updated: {DB}")

    except Exception:
        con.rollback()
        raise

    finally:
        con.close()


if __name__ == "__main__":
    main()
