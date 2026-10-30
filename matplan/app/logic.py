"""Kjernelogikk: innlesing av kvitteringer, normalisering, mønstre, lager og middag."""
import hashlib
import logging
import os
import re
import subprocess
import tempfile
import threading
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from statistics import median

import ai
import db
import ha
import categorize
import parse
from config import MEDIA_DIR, MEDIA_SOURCE_PREFIX

log = logging.getLogger("matplan")

# --- Tid --------------------------------------------------------------------

_tz = None


def tz():
    global _tz
    if _tz is None:
        from zoneinfo import ZoneInfo
        name = os.environ.get("TZ")
        if not name:
            try:
                name = ha.config().get("time_zone")
            except Exception:
                name = None
        try:
            _tz = ZoneInfo(name or "Europe/Oslo")
        except Exception:
            _tz = datetime.now().astimezone().tzinfo
    return _tz


def now():
    return datetime.now(tz())


def today():
    return now().date()


# --- Innlesing --------------------------------------------------------------

def save_receipt(data, source, source_ref=None, uploaded_by=None, fallback_date=None):
    """Lagrer en tolket kvittering. Returnerer (receipt_id, melding)."""
    items = [i for i in (data.get("varer") or []) if (i.get("navn") or "").strip()]
    if not items:
        return None, "Fant ingen varelinjer"
    ref = data.get("ref") or source_ref
    if ref and db.one("SELECT id FROM receipts WHERE source_ref=?", (ref,)):
        return None, "Allerede registrert"
    d = data.get("dato") or (fallback_date or today()).isoformat()
    try:
        date.fromisoformat(d)
    except Exception:
        d = (fallback_date or today()).isoformat()
    store = (data.get("butikk") or "Ukjent").strip()
    # Samme butikk, dato og sum = trolig samme kvittering lastet opp to ganger
    tot = parse.amount(data.get("total"))
    if tot and db.one(
        "SELECT id FROM receipts WHERE store=? AND purchased_on=? AND abs(total-?)<0.01",
        (store, d, tot),
    ):
        return None, "Allerede registrert (samme butikk, dato og sum)"
    rid = db.ex(
        "INSERT INTO receipts(store, purchased_on, total, source, source_ref, uploaded_by) "
        "VALUES(?,?,?,?,?,?)",
        (store, d, tot, source, ref, uploaded_by),
    )
    for it in items:
        name = it["navn"].strip()
        key = parse.norm_key(name)
        m = db.one("SELECT product_id FROM name_map WHERE raw_key=?", (key,))
        db.ex(
            "INSERT INTO items(receipt_id, raw_name, raw_key, qty, unit, price, product_id) "
            "VALUES(?,?,?,?,?,?,?)",
            (rid, name, key, parse.amount(it.get("antall")) or 1, it.get("enhet") or "stk",
             parse.amount(it.get("pris")), m["product_id"] if m else None),
        )
    return rid, f"{store} {d}: {len(items)} varer"


def normalize_pending(progress=None):
    """Kobler ukjente varenavn til produkter via AI, i bolker."""
    pending = db.q(
        "SELECT raw_key, MIN(raw_name) AS raw_name FROM items "
        "WHERE product_id IS NULL GROUP BY raw_key"
    )
    if not pending:
        return 0
    existing = [p["name"] for p in db.q("SELECT name FROM products ORDER BY name")]
    done = 0
    batch_size = 40
    for start in range(0, len(pending), batch_size):
        batch = pending[start:start + batch_size]
        if progress:
            progress(f"Gjenkjenner varer {start + 1}–{start + len(batch)} av {len(pending)}")
        try:
            res = ai.normalize([b["raw_name"] for b in batch], existing)
        except Exception as e:
            log.warning("Normalisering feilet: %s", e)
            continue
        if isinstance(res, dict):
            res = res.get("varer") or res.get("items") or []
        for r in res:
            try:
                b = batch[int(r.get("i"))]
            except (TypeError, ValueError, IndexError):
                continue
            name = (r.get("n") or "").strip()
            if not name:
                pid = 0  # ikke en vare
            else:
                db.ex(
                    "INSERT OR IGNORE INTO products(name, category, shelf_days, food) VALUES(?,?,?,?)",
                    (name, r.get("k") or "Annet", int(r.get("h") or 30), 1 if r.get("m", 1) else 0),
                )
                pid = db.one("SELECT id FROM products WHERE name=?", (name,))["id"]
                if name not in existing:
                    existing.append(name)
            db.ex("INSERT OR REPLACE INTO name_map(raw_key, product_id) VALUES(?,?)", (b["raw_key"], pid))
            db.ex("UPDATE items SET product_id=? WHERE raw_key=? AND product_id IS NULL", (pid, b["raw_key"]))
            done += 1
    return done


def _save_image(content, ext):
    os.makedirs(MEDIA_DIR, exist_ok=True)
    h = hashlib.sha256(content).hexdigest()[:20]
    fname = f"{h}.{ext}"
    path = os.path.join(MEDIA_DIR, fname)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(content)
    mime = "image/png" if ext == "png" else "image/jpeg"
    return {"media_content_id": f"{MEDIA_SOURCE_PREFIX}/{fname}", "media_content_type": mime}


def _pdf_text(content):
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(content)
        f.flush()
        try:
            out = subprocess.run(["pdftotext", "-layout", f.name, "-"],
                                 capture_output=True, timeout=60)
            return out.stdout.decode("utf-8", "replace")
        except Exception:
            return ""


def _pdf_images(content, max_pages=3):
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in.pdf")
        with open(src, "wb") as f:
            f.write(content)
        subprocess.run(["pdftoppm", "-jpeg", "-r", "150", "-f", "1", "-l", str(max_pages),
                        src, os.path.join(d, "p")], capture_output=True, timeout=120)
        imgs = []
        for name in sorted(os.listdir(d)):
            if name.startswith("p") and name.endswith(".jpg"):
                with open(os.path.join(d, name), "rb") as f:
                    imgs.append(f.read())
        return imgs


def process_file(content, mime, filename="", uploaded_by=None, source="upload",
                 source_ref=None, fallback_date=None):
    """Tolker én fil (bilde eller PDF) og lagrer den. Returnerer melding."""
    source_ref = source_ref or "sha:" + hashlib.sha256(content).hexdigest()
    if db.one("SELECT id FROM receipts WHERE source_ref=?", (source_ref,)):
        return "Allerede registrert"
    mime = (mime or "").lower()
    fn = (filename or "").lower()
    if "pdf" in mime or fn.endswith(".pdf"):
        text = _pdf_text(content)
        oda = parse.parse_oda(text)
        if oda:
            data = oda
        elif len(text.strip()) > 80:
            data = ai.parse_receipt_text(text)
        else:
            atts = [_save_image(img, "jpg") for img in _pdf_images(content)]
            if not atts:
                return "Klarte ikke å lese PDF-en"
            data = ai.parse_receipt_image(atts)
    elif mime.startswith("image/") or fn.endswith((".jpg", ".jpeg", ".png")):
        if "heic" in mime or fn.endswith((".heic", ".heif")):
            return "HEIC-bilder støttes ikke – last opp via appen (konverteres automatisk) eller som JPG"
        ext = "png" if ("png" in mime or fn.endswith(".png")) else "jpg"
        data = ai.parse_receipt_image([_save_image(content, ext)])
    else:
        return f"Filtypen støttes ikke ({mime or fn})"
    if not isinstance(data, dict):
        return "Uventet svar fra AI"
    rid, msg = save_receipt(data, source, source_ref, uploaded_by, fallback_date)
    return msg


def process_text(text, uploaded_by=None, source="email", source_ref=None, fallback_date=None):
    oda = parse.parse_oda(text)
    data = oda if oda else ai.parse_receipt_text(text)
    rid, msg = save_receipt(data, source, source_ref, uploaded_by, fallback_date)
    return msg


# --- Mønstre og lager -------------------------------------------------------

def product_stats():
    """Kjøpshistorikk per produkt med anslått lager og når det trolig må kjøpes igjen."""
    rows = db.q(
        "SELECT i.product_id AS pid, r.purchased_on AS d, SUM(COALESCE(i.qty,1)) AS qty, "
        "SUM(COALESCE(i.price,0)) AS spent "
        "FROM items i JOIN receipts r ON r.id=i.receipt_id "
        "WHERE i.product_id > 0 AND r.status='ok' AND r.purchased_on IS NOT NULL "
        "GROUP BY i.product_id, r.purchased_on ORDER BY r.purchased_on"
    )
    products = {p["id"]: p for p in db.q("SELECT * FROM products")}
    pantry = {p["product_id"]: p for p in db.q("SELECT * FROM pantry")}
    # enkeltlinjer med pris per enhet (stk eller kg), butikk og dato
    lines = defaultdict(list)
    for ln in db.q(
        "SELECT i.product_id AS pid, i.price, COALESCE(i.qty,1) AS qty, i.unit, i.raw_name, r.purchased_on AS d, r.store "
        "FROM items i JOIN receipts r ON r.id=i.receipt_id "
        "WHERE i.product_id > 0 AND r.status='ok' AND i.price > 0 AND r.purchased_on IS NOT NULL "
        "ORDER BY r.purchased_on"
    ):
        qty = ln["qty"] if ln["qty"] and ln["qty"] > 0 else 1
        if (ln["unit"] or "").lower() == "kg":
            # veid i butikken: antall er kilo
            basis, per_basis, pack, size = "kg", ln["price"] / qty, ln["price"], f"{qty:g} kg".replace(".", ",")
        else:
            pack = ln["price"] / qty  # pris per pakning
            amt, basis = parse.package_size(ln["raw_name"])
            per_basis = pack / amt if amt else None
            size = _size_label(amt, basis)
        lines[ln["pid"]].append({"d": ln["d"], "store": ln["store"], "price": ln["price"], "pack": pack,
                                 "basis": basis, "ub": per_basis, "size": size})
    events = defaultdict(list)
    for r in rows:
        events[r["pid"]].append((date.fromisoformat(r["d"]), r["qty"] or 1, r["spent"] or 0))
    t = today()
    out = []
    for pid, evs in events.items():
        p = products.get(pid)
        if not p:
            continue
        dates = [d for d, _, _ in evs]
        last = dates[-1]
        since = (t - last).days
        intervals = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
        interval = median(intervals[-8:]) if intervals else None
        recent = sum(1 for d in dates if (t - d).days <= 180)
        q90 = sum(1 for d in dates if (t - d).days < 90)  # antall handler siste kvartal
        shelf = p["shelf_days"] or 30
        pan = pantry.get(pid)
        override = None
        if pan and pan["at"] and pan["at"][:10] >= last.isoformat():
            override = pan["status"]
        gone_after = min(shelf, interval * 1.3) if interval else shelf
        if override:
            in_stock = override == "har"
        else:
            in_stock = since < gone_after
        use_soon = in_stock and override != "har" and shelf <= 14 and since >= shelf * 0.6
        days_left = 0 if override == "tomt" else max(0, round(gone_after - since))
        spent_180 = sum(sp for d, _, sp in evs if (t - d).days <= 180)
        pl_all = lines.get(pid, [])
        # Velg sammenligningsgrunnlag: kr/kg, kr/l, kr/stk eller per pakning
        counts = defaultdict(int)
        for x in pl_all:
            if x["basis"]:
                counts[x["basis"]] += 1
        basis = max(counts, key=counts.get) if counts else None
        if basis and counts[basis] * 2 < len(pl_all):
            basis = None  # for få kjøp med kjent størrelse – sammenlign per pakning
        if basis:
            pl = [{**x, "up": x["ub"]} for x in pl_all if x["basis"] == basis and x["ub"]]
        else:
            pl = [{**x, "up": x["pack"]} for x in pl_all]
        last_prices = [x["pack"] for x in pl_all[-5:]]  # pris per pakning (brukes til handlelista)
        cut90 = (t - timedelta(days=90)).isoformat()
        p90 = [x for x in pl if x["d"] >= cut90]
        ups90 = [x["up"] for x in p90]
        cheapest = min(pl, key=lambda x: x["up"]) if pl else None
        dearest = max(pl, key=lambda x: x["up"]) if pl else None
        avg90 = sum(ups90) / len(ups90) if ups90 else None
        last_up = pl[-1]["up"] if pl else None
        trend = round((last_up - avg90) / avg90 * 100) if (avg90 and last_up and len(ups90) >= 2) else None
        def _pt(x):
            return {"price": round(x["up"], 2), "store": x["store"], "date": x["d"], "size": x["size"],
                    "pack": round(x["pack"], 2)} if x else None
        p90_all = [x for x in pl_all if x["d"] >= cut90]
        price_info = {
            "unit": basis or "pk",  # kg, l, stk eller pk (per pakning)
            "last": round(last_up, 2) if last_up else None,
            "last_store": pl[-1]["store"] if pl else None,
            "last_size": pl[-1]["size"] if pl else None,
            "avg90": round(avg90, 2) if avg90 else None,
            "spent90": round(sum(x["price"] for x in p90_all)),
            "min": _pt(cheapest),
            "max": _pt(dearest),
            "trend": trend,
            "sizes": sorted({x["size"] for x in pl_all if x["size"]}),
            "history": [{"d": x["d"], "p": round(x["up"], 2), "s": x["store"]} for x in pl[-12:]],
        }
        if interval is None:
            rhythm = None
        elif interval <= 8:
            rhythm = "ukentlig"
        elif interval <= 17:
            rhythm = "annenhver uke"
        elif interval <= 40:
            rhythm = "månedlig"
        else:
            rhythm = "sjelden"
        out.append({
            "id": pid,
            "name": p["name"],
            "category": p["category"] or "Annet",
            "food": bool(p["food"]),
            "ignore": bool(p["ignore"]),
            "shelf_days": shelf,
            "times": len(dates),
            "recent": recent,
            "q90": q90,
            "last": last.isoformat(),
            "since": since,
            "interval": round(interval, 1) if interval else None,
            "avg_qty": round(sum(q for _, q, _ in evs) / len(evs), 1),
            "days_left": days_left,
            "rhythm": rhythm,
            "regular": recent >= 3,
            "spent_180": round(spent_180),
            "avg_price": round(sum(last_prices) / len(last_prices), 2) if last_prices else None,
            "spent_90": price_info["spent90"],
            "prices": price_info,
            "override": override,
            "in_stock": in_stock,
            "use_soon": use_soon,
        })
    # varer lagt inn manuelt som «har fra før» (uten kjøp)
    for pid, pan in pantry.items():
        if pid in events or pid not in products or pan["status"] != "har":
            continue
        p = products[pid]
        since = (t - date.fromisoformat(pan["at"][:10])).days
        shelf = p["shelf_days"] or 30
        out.append({
            "id": pid, "name": p["name"], "category": p["category"] or "Annet", "food": bool(p["food"]),
            "ignore": bool(p["ignore"]), "shelf_days": shelf, "times": 0, "recent": 0, "q90": 0,
            "last": pan["at"][:10], "since": since, "interval": None, "avg_qty": 1, "override": "har",
            "in_stock": True, "use_soon": shelf <= 14 and since >= shelf * 0.6,
            "days_left": max(0, round(shelf - since)), "rhythm": None, "regular": False,
            "spent_180": 0, "spent_90": 0, "avg_price": None, "prices": None, "manual": True,
        })
    return out


def snooze_product(pid):
    """«Ikke nå»: skjul forslaget til varen etter vanlig rytme går tom igjen."""
    sn = db.get_setting("shop_snooze") or {}
    sn[str(pid)] = today().isoformat()
    db.set_setting("shop_snooze", sn)


def _snoozed(s, snooze):
    at = snooze.get(str(s["id"]))
    if not at or at < s["last"]:
        return False  # kjøpt etter at det ble skjult → vanlig igjen
    hide_days = max(3, round(s["interval"] or 7))
    return (today() - date.fromisoformat(at)).days < hide_days


def _size_label(amt, basis):
    if not amt:
        return None
    if basis == "kg":
        return f"{amt * 1000:g} g" if amt < 1 else f"{amt:g} kg".replace(".", ",")
    if basis == "l":
        return f"{amt * 100:g} cl" if amt < 0.1 else (f"{amt * 10:g} dl" if amt < 1 else f"{amt:g} l".replace(".", ","))
    return f"{amt:g} stk"


def _list_key(name):
    """Forenklet nøkkel for å kjenne igjen samme vare på handlelista:
    «Lettmelk 1,75 l» → «lettmelk», «Bananer» → «banan»."""
    t = re.sub(r"\d+([.,]\d+)?\s*(x\s*\d+\s*)?(kg|g|gr|l|dl|cl|ml|stk|pk|pakke)?\b", " ", (name or "").lower())
    t = re.sub(r"[^\wæøå ]", " ", t)
    words = [w for w in t.split() if w]
    out = []
    for w in words:
        for suf in ("ene", "er", "en", "et", "e", "r"):
            if len(w) > 4 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.append(w)
    return " ".join(out)


def on_list_matcher(current):
    """Returnerer en funksjon som sier om en vare allerede står på handlelista."""
    keys = [_list_key(c) for c in current if c and c.strip()]
    keyset = set(keys)

    def on_list(name):
        k = _list_key(name)
        if not k:
            return False
        if k in keyset:
            return True
        # «Lettmelk» matcher «lettmelk tine»; «Kaffe» matcher «kaffe evergood»
        for c in keys:
            if c.startswith(k + " ") or k.startswith(c + " "):
                return True
        return False
    return on_list


def shopping_suggestions():
    horizon = int(db.get_setting("shop_horizon") or 2)
    stats = product_stats()
    snooze = db.get_setting("shop_snooze") or {}
    sugg = []
    for s in stats:
        if s["ignore"] or s["override"] == "har" or _snoozed(s, snooze):
            continue
        reason = None
        score = 0
        if s["override"] == "tomt":
            reason = "Markert som tomt"
            score = 10
        elif s["interval"] and s["recent"] >= 3:
            due_in = s["interval"] - s["since"]
            if due_in <= horizon:
                every = round(s["interval"])
                every_txt = "hver dag" if every <= 1 else f"hver {every}. dag"
                since_txt = "i dag" if s["since"] == 0 else (
                    "i går" if s["since"] == 1 else f"for {s['since']} dager siden")
                reason = f"Kjøpes ca. {every_txt}, sist {since_txt}"
                score = s["since"] / max(s["interval"], 1)
        if reason:
            due = 0 if s["override"] == "tomt" else round((s["interval"] or 0) - s["since"])
            sugg.append({**s, "reason": reason, "score": round(score, 2), "due_in": due})
    sugg.sort(key=lambda x: (-x["score"], x["category"], x["name"]))
    regulars = sorted(
        [s for s in stats if s["recent"] >= 3 and not s["ignore"]],
        key=lambda x: (-x["recent"], x["name"]),
    )
    return sugg, regulars


def set_pantry(product_id, status):
    if status in ("har", "tomt"):
        db.ex(
            "INSERT INTO pantry(product_id, status, at) VALUES(?,?,?) "
            "ON CONFLICT(product_id) DO UPDATE SET status=excluded.status, at=excluded.at",
            (product_id, status, now().isoformat(timespec="seconds")),
        )
    else:
        db.ex("DELETE FROM pantry WHERE product_id=?", (product_id,))


def mark_used(names):
    """Markerer varer som brukt opp (fra «Vi lagde denne»)."""
    n = 0
    for name in names or []:
        p = db.one("SELECT id FROM products WHERE name=?", (name,))
        if p:
            set_pantry(p["id"], "tomt")
            n += 1
    return n


# --- Kalender og middag -----------------------------------------------------

def calendar_entities():
    try:
        return [
            {"entity_id": s["entity_id"], "name": s["attributes"].get("friendly_name", s["entity_id"])}
            for s in ha.states() if s["entity_id"].startswith("calendar.")
        ]
    except Exception:
        return []


def _parse_dt(v):
    if isinstance(v, dict):
        v = v.get("dateTime") or v.get("date")
    if not v:
        return None, False
    if len(v) == 10:
        return date.fromisoformat(v), True
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz())
    return dt.astimezone(tz()), False


def calendar_by_day(days):
    start_d = today()
    start = datetime.combine(start_d, time(0), tz())
    end = start + timedelta(days=days)
    chosen = db.get_setting("calendars") or []
    cals = calendar_entities()
    if chosen:
        cals = [c for c in cals if c["entity_id"] in chosen]
    by_day = defaultdict(list)
    errors = []
    for c in cals:
        try:
            evs = ha.calendar_events(c["entity_id"], start, end)
        except Exception as e:
            errors.append(f"{c['name']}: {e}")
            continue
        for ev in evs:
            s, allday = _parse_dt(ev.get("start"))
            e_, _ = _parse_dt(ev.get("end"))
            if s is None:
                continue
            title = (ev.get("summary") or "(uten tittel)").strip()
            if allday:
                last = (e_ - timedelta(days=1)) if e_ else s
                d = s
                while d <= last:
                    if start_d <= d < start_d + timedelta(days=days):
                        by_day[d].append((0, f"{c['name']}: {title} (hele dagen)"))
                    d += timedelta(days=1)
            else:
                d = s.date()
                last_d = e_.date() if e_ else d
                while d <= last_d:
                    if start_d <= d < start_d + timedelta(days=days):
                        if d == s.date():
                            span = f"{s:%H:%M}–{e_:%H:%M}" if e_ and e_.date() == d else f"fra {s:%H:%M}"
                        elif e_ and d == e_.date():
                            span = f"til {e_:%H:%M}"
                        else:
                            span = "hele dagen (pågår)"
                        sort = s.hour * 60 + s.minute if d == s.date() else 0
                        by_day[d].append((sort, f"{c['name']}: {title} {span}"))
                    d += timedelta(days=1)
    return by_day, errors


WEEKDAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]

# --- Middagskalender ---------------------------------------------------------

import re as _re

NOT_HOME = _re.compile(r"\bborte\b|\bhos\s+\w|\bute\b|\brestaurant|\btakeaway|\bbursdag", _re.I)


def title_key(title):
    return _re.sub(r"\s+", " ", (title or "").strip().lower())


def dinner_calendar_entity():
    chosen = db.get_setting("dinner_calendar")
    if chosen:
        return chosen
    for c in calendar_entities():
        if c["name"].strip().lower() in ("middag", "middager", "middagsplan"):
            return c["entity_id"]
    return None


def _dinner_events(start_d, end_d):
    """Henter middager fra middagskalenderen: {dato: tittel}."""
    ent = dinner_calendar_entity()
    if not ent:
        return {}
    start = datetime.combine(start_d, time(0), tz())
    end = datetime.combine(end_d, time(0), tz())
    out = {}
    for ev in ha.calendar_events(ent, start, end):
        s, _ = _parse_dt(ev.get("start"))
        if s is None:
            continue
        d = s if isinstance(s, date) and not isinstance(s, datetime) else s.date()
        title = (ev.get("summary") or "").strip()
        if title:
            out[d] = (out[d] + " / " + title) if d in out else title
    return out


_sync_lock = threading.Lock()


def sync_dinner_calendar(days_back=730):
    """Henter tidligere middager fra kalenderen inn som historikk.
    Returnerer planlagte middager framover {dato: tittel}."""
    ent = dinner_calendar_entity()
    if not ent:
        return {}
    with _sync_lock:
        t = today()
        start_d = t - timedelta(days=days_back)
        events = _dinner_events(start_d, t + timedelta(days=21))
        # Kalenderen er fasit for perioden: erstatt tidligere import (vurderinger ligger per rett)
        db.ex("DELETE FROM meals WHERE source='calendar' AND day>=?", (start_d.isoformat(),))
        n = 0
        for d, title in events.items():
            if d >= t:
                continue
            for part in title.split(" / "):
                if NOT_HOME.search(part):
                    continue
                # Ikke dobbeltregistrer det som allerede er lagt inn via «Vi lagde denne»
                if db.one("SELECT 1 FROM meals WHERE day=? AND title_key=?", (d.isoformat(), title_key(part))):
                    continue
                db.ex(
                    "INSERT INTO meals(day, title, title_key, source) VALUES(?,?,?, 'calendar')",
                    (d.isoformat(), part, title_key(part)),
                )
                n += 1
        db.set_setting("calendar_sync", {"at": now().isoformat(timespec="minutes"), "meals": n, "entity": ent})
        return {d: tt for d, tt in events.items() if d >= t}


def dish_stats(days=365):
    cutoff = (today() - timedelta(days=days)).isoformat()
    rows = db.q(
        "SELECT title_key, MAX(title) AS title, COUNT(*) AS n, MAX(day) AS last "
        "FROM meals WHERE day>=? AND day<=? GROUP BY title_key ORDER BY n DESC, last DESC",
        (cutoff, today().isoformat()),
    )
    ratings = {r["title_key"]: r["rating"] for r in db.q("SELECT * FROM dish_ratings")}
    for r in rows:
        r["rating"] = ratings.get(r["title_key"], 0)
    return rows


def rate_dish(title, rating):
    k = title_key(title)
    db.ex(
        "INSERT INTO dish_ratings(title_key, title, rating) VALUES(?,?,?) "
        "ON CONFLICT(title_key) DO UPDATE SET rating=excluded.rating",
        (k, title.strip(), int(rating)),
    )


def write_dinner(day, title, description=None):
    """Skriver en middag inn i middagskalenderen som heldagsavtale."""
    ent = dinner_calendar_entity()
    if not ent:
        raise RuntimeError("Fant ingen middagskalender – velg den under Oppsett")
    existing = _dinner_events(date.fromisoformat(day), date.fromisoformat(day) + timedelta(days=1))
    if existing:
        raise RuntimeError(f"Det står allerede «{list(existing.values())[0]}» i middagskalenderen den dagen")
    ha.create_all_day_event(ent, day, title, description or "Lagt inn av Matplan")


def short_title(title):
    """Kort kalendertittel i samme stil som familien bruker (f.eks. «Taco», «Currylaks»)."""
    t = _re.split(r"\s+(med|og|m/)\s+", title.strip(), maxsplit=1)[0] if len(title) > 28 else title.strip()
    return t[:1].upper() + t[1:]


# --- Oppskrifter -------------------------------------------------------------

def get_recipe(title, description="", people=None, force=False):
    """Oppskrift for en rett. Huskes per rett; en redigert oppskrift overskrives aldri automatisk."""
    import json as _json
    key = title_key(title)
    row = db.one("SELECT * FROM recipes WHERE title_key=?", (key,))
    if row and (not force or row["edited"]):
        return _json.loads(row["data"])
    s = db.all_settings()
    stats = [p for p in product_stats() if p["food"] and p["in_stock"]]
    pantry = ", ".join(p["name"] for p in stats[:80])
    rec = ai.recipe(title, description, people, s.get("household"), s.get("preferences"), pantry)
    if not isinstance(rec, dict):
        raise RuntimeError("Uventet svar fra AI")
    save_recipe(title, rec, edited=False)
    return rec


def save_recipe(title, rec, edited=True):
    import json as _json
    db.ex(
        "INSERT INTO recipes(title_key, title, data, edited, updated) VALUES(?,?,?,?,datetime('now')) "
        "ON CONFLICT(title_key) DO UPDATE SET title=excluded.title, data=excluded.data, "
        "edited=excluded.edited, updated=excluded.updated",
        (title_key(title), title, _json.dumps(rec, ensure_ascii=False), 1 if edited else 0),
    )


def cached_recipe(title):
    import json as _json
    row = db.one("SELECT data FROM recipes WHERE title_key=?", (title_key(title),))
    return _json.loads(row["data"]) if row else None


def recipe_text(rec, description=""):
    """Ren tekst til kalenderbeskrivelsen."""
    if not rec:
        return (description + "\n\n" if description else "") + "Lagt inn av Matplan"
    lines = []
    if description:
        lines += [description, ""]
    head = "Ingredienser"
    if rec.get("porsjoner"):
        head += f" ({rec['porsjoner']} porsjoner)"
    if rec.get("tid"):
        head += f" · ca. {rec['tid']} min"
    lines.append(head + ":")
    lines += [f"• {i}" for i in rec.get("ingredienser", [])]
    if rec.get("steg"):
        lines += ["", "Slik gjør du:"]
        lines += [f"{n}. {st}" for n, st in enumerate(rec["steg"], 1)]
    if rec.get("tilbehor"):
        lines += ["", f"Tilbehør: {rec['tilbehor']}"]
    if rec.get("tips"):
        lines += ["", f"Tips: {rec['tips']}"]
    lines += ["", "Lagt inn av Matplan"]
    return "\n".join(lines)


# --- Middagsforslag ----------------------------------------------------------

def _dinner_ctx(days, wish="", progress=None):
    if progress:
        progress("Leser middagskalenderen")
    planned, cal_note = {}, None
    try:
        planned = sync_dinner_calendar()
    except Exception as e:
        cal_note = f"Middagskalenderen: {e}"
    if progress:
        progress("Leser kalendere")
    by_day, cal_errors = calendar_by_day(days)
    if cal_note:
        cal_errors.append(cal_note)
    dinner_ent = dinner_calendar_entity()
    dname = _cal_name(dinner_ent) + ":" if dinner_ent else None

    def day_events(d):
        return [t for _, t in sorted(by_day.get(d, [])) if not (dname and t.startswith(dname))]

    lines, planned_lines = [], []
    for i in range(days):
        d = today() + timedelta(days=i)
        evs = day_events(d)
        lines.append(f"{d.isoformat()} ({WEEKDAYS[d.weekday()]}):")
        lines.extend(f"  - {e}" for e in evs) if evs else lines.append("  - ingen avtaler")
        if d in planned:
            planned_lines.append(f"- {d.isoformat()} ({WEEKDAYS[d.weekday()]}): {planned[d]}")
    stats = [s for s in product_stats() if s["food"] and s["in_stock"] and not s["ignore"]]
    stats.sort(key=lambda s: (not s["use_soon"], s["category"], s["name"]))
    pantry = "\n".join(f"- {'*' if s['use_soon'] else ''}{s['name']} ({s['category']})" for s in stats[:160])
    cutoff = (today() - timedelta(days=14)).isoformat()
    recent = ", ".join(dict.fromkeys(m["title"] for m in db.q(
        "SELECT title FROM meals WHERE day>=? AND day<? ORDER BY day DESC", (cutoff, today().isoformat()))))
    dishes = dish_stats()
    s = db.all_settings()
    ctx = {
        "household": s.get("household"),
        "dinner_time": s.get("dinner_time") or "17:00",
        "preferences": s.get("preferences"),
        "wish": wish,
        "calendar": "\n".join(lines),
        "planned": "\n".join(planned_lines),
        "pantry": pantry,
        "recent": recent,
        "frequent": ", ".join(f"{d['title']} ({d['n']})" for d in dishes[:30] if d["n"] >= 2 and d["rating"] >= 0),
        "liked": ", ".join(d["title"] for d in dishes if d["rating"] > 0),
        "disliked": ", ".join(d["title"] for d in dishes if d["rating"] < 0),
        "per_day": 3 if days <= 3 else 2,
    }
    return ctx, planned, day_events, cal_errors, dinner_ent


def _attach_recipes(f):
    rec = cached_recipe(f.get("tittel", ""))
    if rec:
        f["oppskrift"] = rec


def dinner_plan(days=3, wish="", progress=None):
    days = max(1, min(int(days or 3), 7))
    ctx, planned, day_events, cal_errors, dinner_ent = _dinner_ctx(days, wish, progress)
    if progress:
        progress("Spør Claude om forslag")
    res = ai.dinner_plan(ctx)
    if isinstance(res, list):
        res = {"dager": res}
    for dag in res.get("dager", []):
        try:
            d = date.fromisoformat(dag.get("dato"))
            dag["avtaler"] = day_events(d)
            dag["ukedag"] = WEEKDAYS[d.weekday()]
            if d in planned:
                dag["planlagt"] = planned[d]
        except Exception:
            dag["avtaler"] = []
        for f in dag.get("forslag", []):
            _attach_recipes(f)
    res["generert"] = now().isoformat(timespec="minutes")
    res["kalenderfeil"] = cal_errors
    res["middagskalender"] = dinner_ent
    res["dager_antall"] = days
    res["onske"] = wish
    db.set_setting("last_plan", res)
    return res


def replace_suggestion(dato, index, progress=None):
    """Bytter ut ett forslag med et nytt fra Claude."""
    plan = db.get_setting("last_plan") or {}
    dag = next((x for x in plan.get("dager", []) if x.get("dato") == dato), None)
    if not dag or not 0 <= index < len(dag.get("forslag", [])):
        raise RuntimeError("Fant ikke forslaget")
    if dag["forslag"][index].get("i_kalender"):
        raise RuntimeError("Står allerede i middagskalenderen – endre den i kalenderen")
    days = (date.fromisoformat(dato) - today()).days + 1
    ctx, *_ = _dinner_ctx(max(days, 1), plan.get("onske", ""), progress)
    avoid = [f.get("tittel", "") for f in dag["forslag"]] + dag.get("byttet", [])
    info = ", ".join(x for x in [
        f"{dag.get('hjemme')} hjemme" if dag.get("hjemme") else "",
        f"tid: {dag.get('tid')}" if dag.get("tid") else "",
        dag.get("begrunnelse", ""),
    ] if x)
    if progress:
        progress("Spør Claude om et nytt forslag")
    d = date.fromisoformat(dato)
    new = ai.replace_one(ctx, dato, WEEKDAYS[d.weekday()], avoid, info)
    if isinstance(new, list):
        new = new[0] if new else {}
    if isinstance(new, dict) and "forslag" in new:
        new = (new["forslag"] or [{}])[0]
    if not new.get("tittel"):
        raise RuntimeError("Claude kom ikke med noe nytt forslag")
    _attach_recipes(new)
    dag.setdefault("byttet", []).append(dag["forslag"][index].get("tittel", ""))
    dag["forslag"][index] = new
    db.set_setting("last_plan", plan)
    return plan


def _cal_name(entity_id):
    for c in calendar_entities():
        if c["entity_id"] == entity_id:
            return c["name"]
    return entity_id


# --- Manuell registrering -----------------------------------------------------

def suggest_category(name):
    """Kategori for et varenavn uten AI: kjent vare først, ellers ordlisten."""
    name = categorize.pretty_name(name)
    products = db.q("SELECT name, category, shelf_days FROM products")
    match = categorize.closest(name, [p["name"] for p in products])
    if match:
        p = next(x for x in products if x["name"] == match)
        return {"name": name, "existing": match, "category": p["category"] or "Annet",
                "shelf_days": p["shelf_days"] or 30}
    m = db.one(
        "SELECT p.name, p.category, p.shelf_days FROM name_map n JOIN products p ON p.id=n.product_id "
        "WHERE n.raw_key=?", (parse.norm_key(name),))
    if m:
        return {"name": name, "existing": m["name"], "category": m["category"], "shelf_days": m["shelf_days"]}
    cat, shelf, _ = categorize.categorize(name)
    return {"name": name, "existing": None, "category": cat, "shelf_days": shelf}


def add_manual(items, bought=True, user=None):
    """Legger varer til lageret. bought=True registreres som kjøp i dag (teller i mønstrene)."""
    results, rid = [], None
    for it in items:
        raw = (it.get("name") or "").strip()
        if not raw:
            continue
        sug = suggest_category(raw)
        cat = it.get("category") or sug["category"]
        if sug["existing"]:
            p = db.one("SELECT * FROM products WHERE name=?", (sug["existing"],))
            pid = p["id"]
            if it.get("category") and it["category"] != p["category"]:
                db.ex("UPDATE products SET category=? WHERE id=?", (cat, pid))  # husk rettelsen
        else:
            food = 0 if cat in ("Husholdning", "Hygiene", "Barn") else 1
            # valgte brukeren en annen kategori, bruk den kategoriens typiske holdbarhet
            shelf = sug["shelf_days"] if cat == sug["category"] else categorize.DEFAULT_SHELF.get(cat, 30)
            db.ex("INSERT OR IGNORE INTO products(name, category, shelf_days, food) VALUES(?,?,?,?)",
                  (sug["name"], cat, shelf, food))
            pid = db.one("SELECT id FROM products WHERE name=?", (sug["name"],))["id"]
        db.ex("INSERT OR IGNORE INTO name_map(raw_key, product_id) VALUES(?,?)", (parse.norm_key(raw), pid))
        if bought:
            if rid is None:
                rid = db.ex(
                    "INSERT INTO receipts(store, purchased_on, total, source, uploaded_by) VALUES(?,?,?,?,?)",
                    ("Lagt til manuelt", today().isoformat(), None, "manual", user),
                )
            db.ex("INSERT INTO items(receipt_id, raw_name, raw_key, qty, unit, price, product_id) "
                  "VALUES(?,?,?,?,?,?,?)", (rid, raw, parse.norm_key(raw), float(it.get("qty") or 1), "stk", None, pid))
        set_pantry(pid, "har")
        results.append({"name": raw, "product": db.one("SELECT name FROM products WHERE id=?", (pid,))["name"],
                        "category": cat, "existing": bool(sug["existing"])})
    return results
