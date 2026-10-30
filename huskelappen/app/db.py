"""SQLite-lagring for Huskelappen."""
import json
import os
import sqlite3
import threading

from config import DATA_DIR

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS mails(
  msgid TEXT PRIMARY KEY,
  subject TEXT,
  sender TEXT,
  received TEXT,
  result TEXT,
  at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY,
  msgid TEXT,
  day TEXT,                 -- YYYY-MM-DD
  end_day TEXT,             -- siste dag for flerdagers ting, ellers NULL
  start_time TEXT,          -- HH:MM eller NULL (heldag)
  end_time TEXT,
  title TEXT,
  bring TEXT,               -- JSON-liste med ting som skal med
  details TEXT,
  children TEXT,            -- JSON-liste med barnenavn
  kind TEXT,                -- ha_med | spesiell_dag | foreldre | frist | stengt | info
  status TEXT DEFAULT 'pending',   -- pending | approved | rejected
  dedupe_key TEXT UNIQUE,
  source_subject TEXT,
  calendar_note TEXT,       -- resultat av kalenderskriving
  created_at TEXT DEFAULT (datetime('now')),
  decided_at TEXT
);
CREATE INDEX IF NOT EXISTS items_day ON items(day);
CREATE INDEX IF NOT EXISTS items_status ON items(status);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS fixed(
  id INTEGER PRIMARY KEY,
  weekdays TEXT,            -- JSON-liste, 0 = mandag
  title TEXT,
  bring TEXT,
  children TEXT,
  active INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sent(
  key TEXT PRIMARY KEY,
  at TEXT DEFAULT (datetime('now'))
);
"""

DEFAULT_SETTINGS = {
    "kindergarten": "",           # barnehagens navn, f.eks. «Eksempel barnehage»
    # Fylles inn i appen. label = slik barnet skrives i ukeplanen (skiller «Ola AB» fra «Ola CD»).
    # Eksempel: {"name": "Ola", "label": "Ola AB", "groups": ["Avdeling", "Klubb"], "calendar": "calendar.ola"}
    "children": [],
    "ai_entity": "",              # tom = HA sin standard AI-oppgave-entitet
    # Kilder: hvem som sender beskjeder, og hvilke barn de gjelder. Tom = bruk imap_sender fra konfigurasjonen.
    # Eksempel: {"name": "Skolen", "sender": "skolen.no", "children": ["Ola"]}
    "sources": [],
    "notify_service": "",         # f.eks. notify.mobile_app_olas_telefon – hovedmottaker (godkjenning)
    "notify_extra": "",           # en mottaker til, f.eks. partner
    "notify_extra_on": False,     # kryss av for at den andre også får varsler
    "deadline_notify": True,      # varsle før frister
    "deadline_days": 2,           # så mange dager før fristen
    "weekly_summary": True,       # oppsummering av uka i søndagsvarselet og på skjermen
    "weather_entity": "",         # f.eks. weather.forecast_hjem
    "weather_daily": True,        # vær og klesråd hver hverdag, også uten beskjeder
    "todo_entity": "",            # handlelista (samme som Matplan bruker)
    "lunch_days_before": 2,       # legg matpakkemat på lista så mange dager før
    "learn_reject": True,         # avvis automatisk det du har avvist lignende av to ganger
    "learn_approve": False,       # godkjenn automatisk det du alltid godkjenner (tre ganger)
    "review_weekday": 6,          # 0 = mandag … 6 = søndag
    "review_time": "18:00",
    "evening_time": "19:00",      # når morgendagens info vises og varsles
    "hide_time": "09:00",         # når infoen tas bort (barna er levert)
    "evening_notify": True,
    "urgent_notify": True,        # varsle med en gang hvis noe nytt gjelder før neste gjennomgang
    "ignore_subjects": [r"\bSU\b", "samarbeidsutvalg", "regnskap", "parkering"],
}


def conn():
    global _conn
    if _conn is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        _conn = sqlite3.connect(os.path.join(DATA_DIR, "huskelappen.db"), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        _migrate(_conn)
        _conn.commit()
    return _conn


def _migrate(c):
    cols = {r[1] for r in c.execute("PRAGMA table_info(items)")}
    for col in ("weekdays", "source", "auto_note", "decided_by"):
        if col not in cols:
            c.execute(f"ALTER TABLE items ADD COLUMN {col} TEXT")


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


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
    out = json.loads(json.dumps(DEFAULT_SETTINGS))
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


def mark_sent(key):
    """Returnerer True første gang en nøkkel merkes (brukes så varsler bare sendes én gang)."""
    with _lock:
        c = conn()
        cur = c.execute("INSERT OR IGNORE INTO sent(key) VALUES(?)", (key,))
        c.commit()
        return cur.rowcount == 1
