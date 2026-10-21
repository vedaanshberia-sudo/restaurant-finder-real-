"""PART 2: the API (restaurants, locations, sign-in). Run: python app.py  (after seed_db.py)"""
import json, math, os, sqlite3, time
import urllib.parse, urllib.request
from functools import lru_cache
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS
from itsdangerous import URLSafeTimedSerializer, BadSignature
from werkzeug.security import generate_password_hash, check_password_hash

HERE = Path(__file__).resolve().parent
DB = HERE / "restaurants.db"          # restaurants + dishes (rebuilt by seed_db.py)
USERS_DB = HERE / "users.db"          # used only when DATABASE_URL is not set (local testing)
DATABASE_URL = os.environ.get("DATABASE_URL")   # on Render: a free Postgres link, so accounts survive restarts
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
TOKEN_DAYS = 30

app = Flask(__name__)
CORS(app)
signer = URLSafeTimedSerializer(SECRET_KEY)

# Edit these price ranges to suit your Swiggy data
BUDGETS = {"low": (0, 300), "medium": (301, 700), "high": (701, 1000000)}
RADIUS_KM = 15
CATEGORY_COLUMN = {"comfort food": "is_comfort", "protein bowl": "is_protein"}
LEVELS = ["road", "area", "city", "state", "country"]   # most specific first

# ---------- restaurant database (read only) ----------
def query(sql, args=()):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()

# ---------- users database (SQLite locally, Postgres when DATABASE_URL is set) ----------
def run(sql, args=(), fetch=False):
    if DATABASE_URL:
        import psycopg2
        con = psycopg2.connect(DATABASE_URL)
        sql = sql.replace("?", "%s")
    else:
        con = sqlite3.connect(USERS_DB)
    try:
        cur = con.cursor()
        cur.execute(sql, args)
        rows = cur.fetchall() if fetch else None
        con.commit()
        return rows
    finally:
        con.close()

run("""CREATE TABLE IF NOT EXISTS users (
       email TEXT PRIMARY KEY, name TEXT, password_hash TEXT,
       country TEXT, state TEXT, city TEXT, area TEXT, road TEXT,
       lat DOUBLE PRECISION, lng DOUBLE PRECISION, label TEXT)""")

COLS = "name, email, country, state, city, area, road, lat, lng, label"

def get_user(email):
    rows = run(f"SELECT {COLS}, password_hash FROM users WHERE email = ?", (email,), fetch=True)
    return rows[0] if rows else None

def profile(row):
    name, email, country, state, city, area, road, lat, lng, label = row[:10]
    loc = None if lat is None else dict(country=country, state=state, city=city, area=area,
                                        road=road, lat=lat, lng=lng, label=label)
    return {"name": name, "email": email, "location": loc}

def current_email():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        return signer.loads(auth[7:], max_age=TOKEN_DAYS * 86400)
    except BadSignature:
        return None

def read_gps(p):
    try:
        lat, lng = float(p["lat"]), float(p["lng"])
    except (KeyError, TypeError, ValueError):
        return None, None
    return (lat, lng) if -90 <= lat <= 90 and -180 <= lng <= 180 else (None, None)

def km(lat1, lng1, lat2, lng2):
    """Distance in km between two GPS points (haversine formula)."""
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))

# ---------- sign up / sign in ----------
@app.post("/api/signup")
def signup():
    p = request.get_json(silent=True) or {}
    name = str(p.get("name", "")).strip()[:60]
    email = str(p.get("email", "")).strip().lower()[:120]
    password = str(p.get("password", ""))
    if not name or "@" not in email or "." not in email.split("@")[-1]:
        return jsonify(error="Please enter your name and a valid email."), 400
    if len(password) < 8:
        return jsonify(error="Your password needs at least 8 characters."), 400
    if get_user(email):
        return jsonify(error="That email already has an account. Try signing in."), 409
    run("INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
        (email, name, generate_password_hash(password)))
    return jsonify(token=signer.dumps(email), profile=profile(get_user(email)))

@app.post("/api/login")
def login():
    p = request.get_json(silent=True) or {}
    email = str(p.get("email", "")).strip().lower()
    user = get_user(email)
    if not user or not check_password_hash(user[10], str(p.get("password", ""))):
        return jsonify(error="Wrong email or password."), 401
    return jsonify(token=signer.dumps(email), profile=profile(user))

@app.get("/api/me")
def me():
    email = current_email()
    user = get_user(email) if email else None
    if not user:
        return jsonify(error="Please sign in again."), 401
    return jsonify(profile(user))

@app.put("/api/location")
def save_location():
    email = current_email()
    if not email or not get_user(email):
        return jsonify(error="Please sign in again."), 401
    p = request.get_json(silent=True) or {}
    lat, lng = read_gps(p)
    if lat is None:
        return jsonify(error="That location is not valid."), 400
    f = [str(p.get(k, "")).strip()[:80] for k in ("country", "state", "city", "area", "road")]
    run("UPDATE users SET country=?, state=?, city=?, area=?, road=?, lat=?, lng=?, label=? WHERE email=?",
        (*f, lat, lng, str(p.get("label", ""))[:200], email))
    return jsonify(ok=True)

# ---------- turning typed place names into GPS points ----------
@lru_cache(maxsize=512)
def nominatim(q):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "jsonv2", "limit": 1})
    req = urllib.request.Request(url, headers={"User-Agent": "banana-bites-student-project"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    return (float(data[0]["lat"]), float(data[0]["lon"]), data[0].get("display_name", "")) if data else None

MESSAGES = {
    "road": "Found your road. Nearest places first!",
    "area": "I could not find that exact road, so I am using your area.",
    "city": "I am using the middle of your city, so distances are approximate. Add an area for better results.",
    "state": "I could only find your state or region. Check your city spelling.",
}

@app.post("/api/geocode")
def geocode():
    p = request.get_json(silent=True) or {}
    f = {k: str(p.get(k, "")).strip()[:80] for k in LEVELS}
    if not f["country"] or not f["city"]:
        return jsonify(error="Please fill in at least your country and city."), 400
    tried, first = set(), True
    try:
        for i, level in enumerate(LEVELS[:-1]):      # road, area, city, state: stop at first match
            if not f[level]:
                continue
            q = ", ".join(f[l] for l in LEVELS[i:] if f[l])
            if q in tried:
                continue
            tried.add(q)
            if not first:
                time.sleep(1.1)                       # the free service allows 1 request per second
            first = False
            hit = nominatim(q)
            if hit:
                return jsonify(lat=hit[0], lng=hit[1], label=hit[2], precision=level, message=MESSAGES[level])
    except Exception:
        return jsonify(error="The location service is busy. Please try again in a moment."), 502
    return jsonify(error="I could not find that place. Check the spelling of your city and country."), 404

# ---------- restaurant search ----------
@app.get("/api/health")
def health():
    return jsonify(ok=True)

@app.post("/api/recommend")
def recommend():
    p = request.get_json(silent=True) or {}
    cuisine = str(p.get("cuisine", "")).strip()
    category = p.get("category")
    budget = p.get("budget")
    area = str(p.get("area", "")).strip()
    allergies = {str(a).lower() for a in p.get("allergies", [])}

    if not cuisine or category not in CATEGORY_COLUMN or budget not in BUDGETS:
        return jsonify(error="Please choose a cuisine, a food type and a budget."), 400

    low, high = BUDGETS[budget]
    lat, lng = read_gps(p)
    places = [str(x).strip() for x in p.get("places", []) if str(x).strip()][:6]
    if area:
        places.append(area)

    restaurants, mode = [], "area"
    if lat is not None:   # nearest first
        dlat = RADIUS_KM / 111
        dlng = RADIUS_KM / (111 * max(math.cos(math.radians(lat)), 0.01))
        rows = query(
            """SELECT * FROM restaurants
               WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? AND cuisine LIKE ? AND price BETWEEN ? AND ?""",
            (lat - dlat, lat + dlat, lng - dlng, lng + dlng, f"%{cuisine}%", low, high))
        for r in rows:
            r["distance_km"] = round(km(lat, lng, r["lat"], r["lng"]), 1)
        restaurants = sorted((r for r in rows if r["distance_km"] <= RADIUS_KM),
                             key=lambda r: r["distance_km"])[:6]
        mode = "distance"
    if not restaurants:   # fall back to matching area or city names
        mode = "area"
        where = " OR ".join(["(area LIKE ? OR location LIKE ?)"] * len(places)) or "1=1"
        args = [f"%{x}%" for x in places for _ in (0, 1)]
        restaurants = query(
            f"""SELECT * FROM restaurants
                WHERE cuisine LIKE ? AND ({where}) AND price BETWEEN ? AND ?
                ORDER BY rating DESC, number_of_ratings DESC LIMIT 6""",
            (f"%{cuisine}%", *args, low, high))

    col = CATEGORY_COLUMN[category]   # safe: comes from the fixed whitelist above
    candidates = query(
        f"SELECT * FROM dishes WHERE cuisine = ? COLLATE NOCASE AND {col} = 1 ORDER BY RANDOM() LIMIT 100",
        (cuisine,))
    safe = [d for d in candidates if not allergies & set(filter(None, d["allergens"].split(";")))]

    return jsonify(restaurants=restaurants, dishes=safe[:3], searched_dishes=len(candidates), mode=mode)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
