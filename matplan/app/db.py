"""SQLite-lagring for Matplan."""
import json
import os
import sqlite3
import threading

from config import DATA_DIR

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts(
  id INTEGER PRIMARY KEY,
  store TEXT,
  purchased_on TEXT,
  total REAL,
  source TEXT,
  source_ref TEXT UNIQUE,
  uploaded_by TEXT,
  status TEXT DEFAULT 'ok',
  note TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE COLLATE NOCASE,
  category TEXT,
  shelf_days INTEGER,
  food INTEGER DEFAULT 1,
  ignore INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS name_map(
  raw_key TEXT PRIMARY KEY,
  product_id INTEGER
);
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY,
  receipt_id INTEGER REFERENCES receipts(id) ON DELETE CASCADE,
  raw_name TEXT,
  raw_key TEXT,
  qty REAL,
  unit TEXT,
  price REAL,
  product_id INTEGER
);
CREATE INDEX IF NOT EXISTS items_prod ON items(product_id);
CREATE INDEX IF NOT EXISTS items_rec ON items(receipt_id);
CREATE INDEX IF NOT EXISTS items_key ON items(raw_key);
CREATE TABLE IF NOT EXISTS pantry(
  product_id INTEGER PRIMARY KEY,
  status TEXT,
  at TEXT
);
CREATE TABLE IF NOT EXISTS meals(
  id INTEGER PRIMARY KEY,
  day TEXT,
  title TEXT,
  by_user TEXT,
  rating INTEGER DEFAULT 0,
  data TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dish_ratings(
  title_key TEXT PRIMARY KEY,
  title TEXT,
  rating INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS recipes(
  title_key TEXT PRIMARY KEY,
  title TEXT,
  data TEXT,
  edited INTEGER DEFAULT 0,
  updated TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS mail_seen(
  msgid TEXT PRIMARY KEY,
  result TEXT,
  at TEXT DEFAULT (datetime('now'))
);
"""

DEFAULT_SETTINGS = {
    "ai_entity": "",            # tom = HA sin standard AI-oppgave-entitet
    "todo_entity": "",          # f.eks. todo.handleliste
    "calendars": [],            # tom = alle kalendere
    "household": "",
    "preferences": "",
    "dinner_time": "17:00",
    "shop_horizon": 2,          # dager til neste handel – varer som går tom innen da foreslås
    "dinner_calendar": "",      # tom = finn kalender som heter «Middag»
    "write_calendar": True,     # skriv valgte middager inn i middagskalenderen
    "receipt_email": "",        # adresse familien videresender kvitteringer til (vises i bruksanvisningen)
    "import_lists": [],         # andre gjøremålslister (f.eks. Google Keep) det kan hentes varer fra
    "import_remove": True,      # fjern varene fra kildelista etter henting
}


def conn():
    global _conn
    if _conn is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        _conn = sqlite3.connect(
            os.path.join(DATA_DIR, "matplan.db"), check_same_thread=False
        )
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        _migrate(_conn)
        _conn.commit()
    return _conn


def _migrate(c):
    cols = {r[1] for r in c.execute("PRAGMA table_info(meals)")}
    if "source" not in cols:
        c.execute("ALTER TABLE meals ADD COLUMN source TEXT DEFAULT 'app'")
    if "title_key" not in cols:
        c.execute("ALTER TABLE meals ADD COLUMN title_key TEXT")
        c.execute("UPDATE meals SET title_key=lower(trim(title))")
    # flytt gamle vurderinger per middag over til vurdering per rett
    c.execute(
        "INSERT OR IGNORE INTO dish_ratings(title_key, title, rating) "
        "SELECT lower(trim(title)), title, rating FROM meals WHERE rating != 0"
    )
    # rettelser av lageranslaget («Har»/«Tomt»), som appen lærer av
    c.execute("CREATE TABLE IF NOT EXISTS pantry_log(id INTEGER PRIMARY KEY, product_id INTEGER, status TEXT, "
              "at TEXT, since INTEGER, base_life REAL)")
    c.execute("CREATE INDEX IF NOT EXISTS pantry_log_pid ON pantry_log(product_id)")
    c.execute("CREATE INDEX IF NOT EXISTS meals_day ON meals(day)")
    c.execute("CREATE INDEX IF NOT EXISTS meals_key ON meals(title_key)")


def q(sql, args=()):
    with _lock:
        cur = conn().execute(sql, args)
        return [dict(r) for r in cur.fetchall()]


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql, args=()):
    with _lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def get_setting(key):
    row = one("SELECT value FROM settings WHERE key=?", (key,))
    if row is None:
        return DEFAULT_SETTINGS.get(key)
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]


def all_settings():
    out = dict(DEFAULT_SETTINGS)
    for r in q("SELECT key, value FROM settings"):
        try:
            out[r["key"]] = json.loads(r["value"])
        except Exception:
            out[r["key"]] = r["value"]
    return out


def set_setting(key, value):
    ex(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )
