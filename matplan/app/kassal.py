"""Prissammenligning mellom kjeder via Kassalapp-API-et (kassal.app).

Krever en gratis API-nøkkel fra kassal.app/api, lagt inn i app-konfigurasjonen.
Kun lesing: søk etter varer og hent dagens priser + 30 dagers historikk.
"""
import json
import logging
import os
import re
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import db
import parse
from config import load_options

log = logging.getLogger("matplan.kassal")
BASE = os.environ.get("KASSAL_BASE", "https://kassal.app/api/v1")

SCHEMA = """
CREATE TABLE IF NOT EXISTS kassal_map(item_key TEXT PRIMARY KEY, ean TEXT, name TEXT, updated TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS kassal_cache(key TEXT PRIMARY KEY, data TEXT, fetched REAL);
"""
_ready = False


class KassalError(Exception):
    pass


# Kassalapp tillater 60 kall per minutt. Vi holder oss på 50 for å ha god margin.
RATE_LIMIT = int(os.environ.get("KASSAL_RATE", "50"))
_calls = []
_rate_lock = threading.Lock()
on_wait = None  # settes av jobben for å vise «venter»-melding


def _throttle():
    with _rate_lock:
        while True:
            now = time.time()
            while _calls and now - _calls[0] > 60:
                _calls.pop(0)
            if len(_calls) < RATE_LIMIT:
                _calls.append(now)
                return
            wait = 60 - (now - _calls[0]) + 0.5
            if on_wait:
                try:
                    on_wait(f"Venter på Kassalapp (maks {RATE_LIMIT} kall i minuttet) – {int(wait) + 1} s")
                except Exception:
                    pass
            time.sleep(min(wait, 5))


def _init():
    global _ready
    if not _ready:
        db.conn().executescript(SCHEMA)
        _ready = True


def api_key():
    return (load_options().get("kassalapp_api_key") or "").strip()


def configured():
    return bool(api_key())


def _req(method, path, body=None, attempt=0):
    key = api_key()
    if not key:
        raise KassalError("Kassalapp er ikke satt opp – legg inn API-nøkkel i app-konfigurasjonen i Home Assistant")
    data = json.dumps(body).encode() if body is not None else None
    _throttle()
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json",
        "Content-Type": "application/json", "User-Agent": "Matplan (Home Assistant)",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        if e.code == 429 and attempt < 3:
            try:
                wait = int(e.headers.get("Retry-After") or 0)
            except ValueError:
                wait = 0
            wait = min(max(wait, 10 * (attempt + 1)), 60)
            if on_wait:
                try:
                    on_wait(f"Kassalapp ba oss vente – prøver igjen om {wait} s")
                except Exception:
                    pass
            time.sleep(wait)
            return _req(method, path, body, attempt + 1)
        if e.code == 429:
            raise KassalError("Kassalapp har for mange forespørsler akkurat nå – prøv igjen om et minutt")
        if e.code == 401:
            raise KassalError("Kassalapp avviste API-nøkkelen")
        if e.code == 404:
            return None
        raise KassalError(f"Kassalapp svarte {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
    except urllib.error.URLError as e:
        raise KassalError(f"Får ikke kontakt med Kassalapp: {e.reason}")


def _cached(key, max_age, fn):
    _init()
    row = db.one("SELECT * FROM kassal_cache WHERE key=?", (key,))
    if row and time.time() - row["fetched"] < max_age:
        return json.loads(row["data"])
    data = fn()
    db.ex("INSERT OR REPLACE INTO kassal_cache(key, data, fetched) VALUES(?,?,?)",
          (key, json.dumps(data, ensure_ascii=False), time.time()))
    return data


def search(query, size=12):
    q = (query or "").strip()
    if len(q) < 3:
        return []

    def fetch():
        res = _req("GET", "/products?" + urllib.parse.urlencode(
            {"search": q, "size": size, "unique": 1, "exclude_without_ean": 1})) or {}
        out = []
        for p in res.get("data") or []:
            if not p.get("ean"):
                continue
            st = p.get("store") or {}
            if isinstance(st, list):
                st = st[0] if st else {}
            out.append({"ean": p["ean"], "name": p.get("name") or "", "brand": p.get("brand"),
                        "price": _f(p.get("current_price")), "store": st.get("name"),
                        "image": p.get("image"), "weight": p.get("weight"), "weight_unit": p.get("weight_unit")})
        return out
    return _cached("s:" + q.lower(), 24 * 3600, fetch)


GENERIC = {"stk", "pk", "pakke", "ca", "g", "kg", "l", "dl", "cl", "ml", "og", "med", "i", "per", "norge",
           "fersk", "ferske", "pose", "boks", "fett", "lett"}


def _f(v):
    """Pris fra Kassalapp som tall – API-et sender noen ganger tekst («29.90») eller tomt."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    if isinstance(v, dict):
        return _f(v.get("price"))
    if isinstance(v, list):
        vals = [x for x in (_f(i) for i in v) if x is not None]
        return min(vals) if vals else None
    try:
        x = float(str(v).replace(",", ".").replace("kr", "").strip())
        return x if x > 0 else None
    except ValueError:
        return None


def _tokens(s):
    return {w for w in re.findall(r"[a-zæøå]+", (s or "").lower()) if len(w) > 1 and w not in GENERIC}


def _stem_hit(a, b):
    """Ordstamme-treff: «bananer» ~ «banan», «tomater» ~ «tomat»."""
    return a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b[:5]) or b.startswith(a[:5])))


def _score(item_tokens, hint_tokens, result):
    rt = _tokens(result["name"] + " " + (result.get("brand") or ""))
    name_hits = sum(1 for t in item_tokens if any(_stem_hit(t, r) for r in rt))
    hint_hits = sum(1 for t in hint_tokens if any(_stem_hit(t, r) for r in rt))
    return name_hits * 3 + hint_hits, name_hits


# Ord som betyr at det er et annet produkt enn råvaren («blåbærsaft», «paprikachips»)
FLAVOR = {"saft", "juice", "chips", "smak", "smaksatt", "drikke", "sirup", "godteri", "snacks", "yoghurt",
          "smoothie", "dressing", "saus", "krydder", "pulver", "is", "te", "nektar", "syltetøy", "gelé", "pålegg",
          "kjeks", "bar", "brus", "shot", "sukkerfri", "mix", "miks", "kake", "muffins", "sjokolade", "nøttemiks"}


# kategorier der en vare i liter nesten alltid er feil (drikke/saft i stedet for råvaren)
NOT_LIQUID = {"Snacks og godteri", "Frukt og grønt", "Kjøtt", "Fisk og sjømat", "Brød og bakst", "Tørrvarer",
              "Pålegg", "Frysevarer", "Husholdning", "Hygiene"}


def _score2(item_t, hint_t, sizes, result, category=None):
    rt = _tokens(result["name"] + " " + (result.get("brand") or ""))
    exact = head = modifier = 0
    for t in item_t:
        if t in rt:
            exact += 1
        elif len(t) >= 4 and any(r.endswith(t) for r in rt):
            head += 1      # «isbergsalat» er en salat, «frokostkaffe» er kaffe
        elif any(_stem_hit(t, r) for r in rt):
            modifier += 1  # «blåbærsaft» er saft, ikke blåbær
    if exact + head + modifier == 0:
        return None  # varenavnet må treffe
    hint_hits = sum(1 for t in hint_t if t in rt)
    flavor = sum(1 for t in rt if (t in FLAVOR or any(t.endswith(f) and len(t) > len(f) + 2 for f in ("saft", "chips", "juice", "yoghurt", "smak")))
                 and t not in item_t and t not in hint_t)
    amt, basis = parse.package_size(result["name"])
    size_bonus = 2 if (amt and sizes and any(b == basis and abs(a - amt) <= 0.1 * a for a, b in sizes)) else 0
    liquid_pen = 3 if (basis == "l" and category in NOT_LIQUID) else 0
    return exact * 4 + head * 2.5 + modifier * 0.5 + hint_hits + size_bonus - flavor * 4 - liquid_pen


def candidates(name, hints=(), category=None):
    """Mulige Kassalapp-produkter for en vare, best først: [(poeng, produkt)]."""
    hints = [h for h in hints if h]
    item_t = _tokens(name)
    hint_t = set().union(*[_tokens(h) for h in hints]) if hints else set()
    sizes = [ps for ps in (parse.package_size(h) for h in hints) if ps[0]]
    pool, seen = [], set()
    queries = ([re.sub(r",.*$", "", hints[0])[:60]] if hints else []) + [name]
    for q in queries:
        for r in search(q):
            if r["ean"] not in seen:
                seen.add(r["ean"])
                pool.append(r)
    scored = [(sc, r) for r in pool for sc in [_score2(item_t, hint_t, sizes, r, category)] if sc is not None]
    scored.sort(key=lambda x: -x[0])
    return scored[:6]


def best_ean(name, hint=None):
    """Bakoverkompatibel: beste kandidat uten prissjekk."""
    _init()
    saved = db.one("SELECT * FROM kassal_map WHERE item_key=?", (parse.norm_key(name),))
    if saved:
        return {"ean": saved["ean"], "name": saved["name"]}, "valgt før", []
    c = candidates(name, [hint] if hint else [])
    return (c[0][1], "trolig" if hint else "forslag", [r for _, r in c]) if c else (None, None, [])


def saved_choice(name):
    _init()
    row = db.one("SELECT * FROM kassal_map WHERE item_key=?", (parse.norm_key(name),))
    return {"ean": row["ean"], "name": row["name"]} if row else None


def remember(name, ean, kname):
    _init()
    db.ex("INSERT OR REPLACE INTO kassal_map(item_key, ean, name, updated) VALUES(?,?,?,datetime('now'))",
          (parse.norm_key(name), str(ean), kname or ""))


def bulk_prices(eans, days=30):
    eans = list(dict.fromkeys(str(e) for e in eans if e))
    out = {}
    for i in range(0, len(eans), 100):
        chunk = eans[i:i + 100]
        key = "b:" + ",".join(sorted(chunk)) + f":{days}"
        data = _cached(key, 6 * 3600, lambda c=chunk: _req("POST", "/products/prices-bulk",
                                                            {"eans": c, "days": days, "aggregation": "min"}) or {})
        for d in data.get("data") or []:
            out[str(d.get("ean"))] = d
    return out


def _store_count(d):
    return sum(1 for st in (d or {}).get("stores") or [] if _f(st.get("current_price")) is not None)


def choose(rows, progress=None):
    """Velger produkt per vare: blant kandidatene vinner den som både ligner mest og har priser i flest kjeder."""
    for i, r in enumerate(rows):
        if progress:
            progress(f"Finner «{r['name']}» hos Kassalapp ({i + 1}/{len(rows)})")
        fixed = saved_choice(r["name"])
        if fixed:
            r["ean"], r["kname"], r["how"], r["cands"] = fixed["ean"], fixed["name"], "valgt før", []
        else:
            r["cands"] = candidates(r["name"], r.get("hints") or [], r.get("category"))
    if progress:
        progress("Henter priser fra kjedene")
    all_eans = [r["ean"] for r in rows if r.get("ean")] + [c["ean"] for r in rows for _, c in r.get("cands") or []]
    prices = bulk_prices(all_eans)
    for r in rows:
        if r.get("how") == "valgt før":
            continue
        best = None
        for sc, c in r.get("cands") or []:
            n = _store_count(prices.get(str(c["ean"])))
            rank = (n > 0, sc + 1.5 * min(n, 5))
            if best is None or rank > best[0]:
                best = (rank, c, n)
        if best:
            r["ean"], r["kname"] = best[1]["ean"], best[1]["name"]
            r["how"] = "trolig" if best[2] else "ingen priser"
        else:
            r["ean"], r["kname"], r["how"] = None, None, "ikke funnet"
        r.pop("cands", None)
    return rows, prices


def compare(rows, prices=None):
    """rows: [{name, ean, kname}] → sammenligning per kjede."""
    if prices is None:
        prices = bulk_prices([r["ean"] for r in rows if r.get("ean")])
    stores = {}
    items = []
    for r in rows:
        d = prices.get(str(r.get("ean"))) if r.get("ean") else None
        per_store = {}
        if d:
            hist = {}
            for h in d.get("price_history") or []:
                hp = _f(h.get("price"))
                if hp is not None:
                    hist.setdefault(h.get("store"), []).append(hp)
            for st in d.get("stores") or []:
                cur = _f(st.get("current_price"))
                if cur is None:
                    continue
                code = st.get("store")
                stores.setdefault(code, {"code": code, "name": st.get("name") or code})
                hs = hist.get(code) or []
                typical = statistics.median(hs) if len(hs) >= 3 else None
                per_store[code] = {
                    "price": cur,
                    "unit_price": _f(st.get("current_unit_price")),
                    "unit": st.get("current_unit_price_unit"),
                    "low30": min(hs) if hs else None,
                    "typical": typical,
                    # mer enn 10 % under vanlig nivå siste 30 dager → trolig tilbud
                    "deal": bool(typical and cur < typical * 0.9),
                    "checked": st.get("last_checked"),
                }
        cheapest = min(per_store.items(), key=lambda kv: kv[1]["price"])[0] if per_store else None
        items.append({**r, "kname": (d or {}).get("name") or r.get("kname"), "stores": per_store, "cheapest": cheapest})
    items.sort(key=lambda i: not i["stores"])  # funne varer først, «ikke funnet» nederst
    # totaler: bare varer som finnes i kjeden; viser også hvor mange som manglet
    found_items = [i for i in items if i["stores"]]
    totals = []
    for code, st in stores.items():
        have = [i for i in found_items if code in i["stores"]]
        totals.append({**st, "total": round(sum(i["stores"][code]["price"] for i in have), 2),
                       "count": len(have), "missing": len(found_items) - len(have),
                       "deals": sum(1 for i in have if i["stores"][code]["deal"])})
    # rangér: flest varer først, så lavest sum
    totals.sort(key=lambda t: (t["missing"], t["total"]))
    return {"items": items, "stores": totals, "found": len(found_items)}
