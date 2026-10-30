"""Finner riktig Oda-produkt for varene på handlelista.

Rekkefølge:
  1. Vare du har valgt før (huskes)
  2. Det dere pleier å kjøpe: Oda-kvitteringer + ordrehistorikk hos Oda
  3. Søk hos Oda – foretrekker produkter dere har kjøpt før
"""
import json
import logging
import re
import time

import db
import parse
from oda import OdaError, client

log = logging.getLogger("matplan.oda")

SCHEMA = """
CREATE TABLE IF NOT EXISTS oda_map(item_key TEXT PRIMARY KEY, oda_id INTEGER, oda_name TEXT, updated TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS oda_history(product_id INTEGER PRIMARY KEY, name TEXT, times INTEGER);
CREATE TABLE IF NOT EXISTS oda_prices(product_id INTEGER PRIMARY KEY, data TEXT, fetched REAL);
"""
_ready = False


def _init():
    global _ready
    if not _ready:
        db.conn().executescript(SCHEMA)
        _ready = True


def _n(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9æøå%,. ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def refresh_history(force=False, progress=None):
    _init()
    last = db.get_setting("oda_history_at") or 0
    if not force and time.time() - float(last) < 24 * 3600 and db.one("SELECT 1 FROM oda_history"):
        return
    if progress:
        progress("Leser tidligere Oda-ordrer")
    hist = client.order_history()
    db.ex("DELETE FROM oda_history")
    for pid, e in hist.items():
        db.ex("INSERT OR REPLACE INTO oda_history(product_id, name, times) VALUES(?,?,?)", (pid, e["name"], e["times"]))
    db.set_setting("oda_history_at", time.time())


def _history():
    return db.q("SELECT * FROM oda_history ORDER BY times DESC")


def _from_receipts(name, history):
    """Oda-produkt via Oda-kvitteringene for den samme Matplan-varen."""
    prod = db.one("SELECT id FROM products WHERE name=?", (name,))
    if not prod:
        m = db.one("SELECT product_id FROM name_map WHERE raw_key=?", (parse.norm_key(name),))
        prod = {"id": m["product_id"]} if m and m["product_id"] else None
    if not prod:
        return None
    raws = [r["raw_name"] for r in db.q(
        "SELECT i.raw_name, COUNT(*) n FROM items i JOIN receipts r ON r.id=i.receipt_id "
        "WHERE i.product_id=? AND r.store='Oda' GROUP BY i.raw_key ORDER BY n DESC LIMIT 5", (prod["id"],))]
    best = None
    for raw in raws:
        rn = _n(raw)[:28]
        if len(rn) < 4:
            continue
        for h in history:
            hn = _n(h["name"])
            if hn.startswith(rn) or rn.startswith(hn[:28]):
                if not best or h["times"] > best["times"]:
                    best = h
    return best


def price_for(oda_id, oda_name, max_age=6 * 3600):
    """Dagens pris og ev. tilbud for et Oda-produkt (søker på navnet og finner riktig ID)."""
    _init()
    row = db.one("SELECT * FROM oda_prices WHERE product_id=?", (oda_id,))
    if row and time.time() - row["fetched"] < max_age:
        return json.loads(row["data"])
    data = None
    query = re.sub(r",.*$", "", oda_name or "").strip() or oda_name
    for q in (oda_name, query):
        if not q:
            continue
        try:
            res = client.search(q, limit=20)
        except OdaError:
            break
        hit = next((r for r in res if r["id"] == oda_id), None)
        if hit:
            data = hit
            break
    if data:
        db.ex("INSERT OR REPLACE INTO oda_prices(product_id, data, fetched) VALUES(?,?,?)",
              (oda_id, json.dumps(data, ensure_ascii=False), time.time()))
    return data


def best_for(name, history=None, allow_search=True):
    """Beste Oda-produkt for en vare (uten pris). Returnerer (produkt, hvordan, alternativer)."""
    _init()
    history = history if history is not None else _history()
    saved = db.one("SELECT * FROM oda_map WHERE item_key=?", (parse.norm_key(name),))
    if saved:
        return {"id": saved["oda_id"], "name": saved["oda_name"]}, "valgt før", []
    h = _from_receipts(name, history)
    if h:
        return {"id": h["product_id"], "name": h["name"]}, "pleier å kjøpe", []
    if not allow_search:
        return None, None, []
    res = client.search(name)
    hist_ids = {x["product_id"]: x for x in history}
    known = [r for r in res if r["id"] in hist_ids]
    if known:
        return max(known, key=lambda r: hist_ids[r["id"]]["times"]), "har kjøpt før", res
    avail = [r for r in res if r.get("available")] or res
    return (avail[0], "forslag", res) if avail else (None, None, res)


def match(names, progress=None):
    _init()
    try:
        refresh_history(progress=progress)
    except OdaError as e:
        log.warning("Ordrehistorikk fra Oda: %s", e)
    history = _history()
    hist_ids = {h["product_id"]: h for h in history}
    out = []
    for i, name in enumerate(names):
        if progress:
            progress(f"Finner «{name}» hos Oda ({i + 1}/{len(names)})")
        key = parse.norm_key(name)
        row = {"name": name, "quantity": 1, "oda": None, "how": None, "alternatives": []}
        saved = db.one("SELECT * FROM oda_map WHERE item_key=?", (key,))
        if saved:
            row["oda"] = {"id": saved["oda_id"], "name": saved["oda_name"]}
            row["how"] = "valgt før"
        else:
            h = _from_receipts(name, history)
            if h:
                row["oda"] = {"id": h["product_id"], "name": h["name"]}
                row["how"] = "pleier å kjøpe"
        if not row["oda"]:
            try:
                res = client.search(name)
            except OdaError as e:
                row["error"] = str(e)
                res = []
            row["alternatives"] = res
            known = [r for r in res if r["id"] in hist_ids]
            if known:
                pick = max(known, key=lambda r: hist_ids[r["id"]]["times"])
                row["oda"], row["how"] = pick, "har kjøpt før"
            else:
                avail = [r for r in res if r.get("available")] or res
                if avail:
                    row["oda"], row["how"] = avail[0], "forslag"
            time.sleep(0.3)
        if row["oda"] and row["oda"].get("price") is None and row["oda"].get("id"):
            p = price_for(row["oda"]["id"], row["oda"].get("name"))
            if p:
                row["oda"] = {**p, "name": p.get("name") or row["oda"].get("name")}
        out.append(row)
    return out


def remember(name, oda_id, oda_name):
    _init()
    db.ex("INSERT OR REPLACE INTO oda_map(item_key, oda_id, oda_name, updated) VALUES(?,?,?,datetime('now'))",
          (parse.norm_key(name), int(oda_id), oda_name or ""))
