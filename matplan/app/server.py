"""HTTP-server for Matplan (kjører bak Home Assistant ingress)."""
import base64
import json
import logging
import mimetypes
import os
import re
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import db
import ha
import logic
import mailer
from config import DEV, INGRESS_IP, PORT

log = logging.getLogger("matplan.http")
STATIC = os.path.join(os.path.dirname(__file__), "static")

# --- Bakgrunnsjobber --------------------------------------------------------

JOBS = {}


def start_job(kind, fn):
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "kind": kind, "status": "running", "progress": "Starter", "result": None, "error": None}
    JOBS[jid] = job

    def progress(msg):
        job["progress"] = msg

    def run():
        try:
            job["result"] = fn(progress)
            job["status"] = "done"
        except Exception as e:
            log.error("Jobb %s feilet: %s\n%s", kind, e, traceback.format_exc())
            job["error"] = str(e)
            job["status"] = "error"
        # hold listen kort
        if len(JOBS) > 50:
            for k in list(JOBS)[:-50]:
                JOBS.pop(k, None)

    threading.Thread(target=run, daemon=True).start()
    return job


# --- Ruter ------------------------------------------------------------------

ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


@route("GET", "/api/state")
def api_state(req, body):
    s = db.all_settings()
    s.pop("last_plan", None)
    counts = db.one(
        "SELECT (SELECT COUNT(*) FROM receipts) AS receipts, "
        "(SELECT COUNT(*) FROM products) AS products, "
        "(SELECT COUNT(DISTINCT raw_key) FROM items WHERE product_id IS NULL) AS pending"
    )
    return {
        "user": req.user,
        "settings": s,
        "counts": counts,
        "mail": {"enabled": mailer.enabled(), **mailer.status},
        "dinner_calendar": logic.dinner_calendar_entity(),
        "calendar_sync": db.get_setting("calendar_sync"),
        "today": logic.today().isoformat(),
    }


@route("GET", "/api/entities")
def api_entities(req, body):
    try:
        st = ha.states()
    except Exception as e:
        raise ApiError(str(e), 502)

    def pick(prefix):
        return [
            {"entity_id": s["entity_id"], "name": s["attributes"].get("friendly_name", s["entity_id"])}
            for s in st if s["entity_id"].startswith(prefix)
        ]
    return {"ai_task": pick("ai_task."), "todo": pick("todo."), "calendar": pick("calendar.")}


@route("GET", "/api/settings")
def api_settings(req, body):
    s = db.all_settings()
    s.pop("last_plan", None)
    return s


@route("POST", "/api/settings")
def api_settings_save(req, body):
    allowed = set(db.DEFAULT_SETTINGS)
    for k, v in (body or {}).items():
        if k in allowed:
            db.set_setting(k, v)
    return api_settings(req, None)


@route("GET", "/api/receipts")
def api_receipts(req, body):
    """Kvitteringer med søk i varelinjer/butikk, butikkfilter og sortering."""
    qs = req.query
    q = (qs.get("q") or "").strip().casefold()
    store = (qs.get("store") or "").strip()
    sort = qs.get("sort") or "date_desc"
    receipts = db.q(
        "SELECT r.*, COUNT(i.id) AS items FROM receipts r LEFT JOIN items i ON i.receipt_id=r.id "
        "WHERE r.source != 'manual' GROUP BY r.id"
    )
    stores = {}
    for r in receipts:
        stores[r["store"]] = stores.get(r["store"], 0) + 1
    if store:
        receipts = [r for r in receipts if r["store"] == store]
    matched_total = 0.0
    if q:
        # casefold i Python, så æ/ø/å matcher uavhengig av store/små bokstaver
        terms = q.split()
        lines = db.q(
            "SELECT i.receipt_id, i.raw_name, i.qty, i.price, p.name AS product "
            "FROM items i LEFT JOIN products p ON p.id=i.product_id"
        )
        hits = {}
        for ln in lines:
            hay = f"{ln['raw_name'] or ''} {ln['product'] or ''}".casefold()
            if all(t in hay for t in terms):
                hits.setdefault(ln["receipt_id"], []).append(ln)
        out = []
        for r in receipts:
            store_hit = all(t in (r["store"] or "").casefold() for t in terms)
            if r["id"] in hits or store_hit:
                r["hits"] = [
                    {"name": h["raw_name"], "product": h["product"], "qty": h["qty"], "price": h["price"]}
                    for h in hits.get(r["id"], [])
                ]
                matched_total += sum(h["price"] or 0 for h in hits.get(r["id"], []))
                out.append(r)
        receipts = out
    # stabil sortering: først dato (nyeste først), deretter valgt nøkkel
    receipts.sort(key=lambda r: (r["purchased_on"] or "", r["id"]), reverse=True)
    if sort == "date_asc":
        receipts.reverse()
    elif sort == "store":
        receipts.sort(key=lambda r: (r["store"] or "").casefold())
    elif sort == "total":
        receipts.sort(key=lambda r: r["total"] or 0, reverse=True)
    return {
        "receipts": receipts[:500],
        "count": len(receipts),
        "sum": round(sum(r["total"] or 0 for r in receipts), 2),
        "matched_sum": round(matched_total, 2),
        "stores": sorted(({"name": k, "count": v} for k, v in stores.items()), key=lambda x: -x["count"]),
    }


@route("GET", r"/api/receipts/(\d+)")
def api_receipt(req, body, rid):
    r = db.one("SELECT * FROM receipts WHERE id=?", (rid,))
    if not r:
        raise ApiError("Fant ikke kvitteringen", 404)
    r["items"] = db.q(
        "SELECT i.*, p.name AS product, p.category FROM items i "
        "LEFT JOIN products p ON p.id=i.product_id WHERE receipt_id=? ORDER BY i.id",
        (rid,),
    )
    return r


@route("DELETE", r"/api/receipts/(\d+)")
def api_receipt_delete(req, body, rid):
    db.ex("DELETE FROM items WHERE receipt_id=?", (rid,))
    db.ex("DELETE FROM receipts WHERE id=?", (rid,))
    return {"ok": True}


@route("POST", "/api/upload")
def api_upload(req, body):
    files = (body or {}).get("files") or []
    if not files:
        raise ApiError("Ingen filer")
    user = req.user.get("name")
    decoded = []
    for f in files:
        try:
            decoded.append((base64.b64decode(f["data"]), f.get("type", ""), f.get("name", "")))
        except Exception:
            raise ApiError(f"Kunne ikke lese {f.get('name')}")

    def work(progress):
        results = []
        for i, (content, mime, name) in enumerate(decoded):
            progress(f"Leser kvittering {i + 1} av {len(decoded)}")
            try:
                results.append({"file": name, "result": logic.process_file(content, mime, name, user)})
            except Exception as e:
                results.append({"file": name, "result": f"Feil: {e}"})
        progress("Gjenkjenner varer")
        logic.normalize_pending(progress)
        return results

    return start_job("upload", work)


@route("POST", "/api/mail/poll")
def api_mail_poll(req, body):
    return start_job("mail", lambda p: mailer.poll(p))


@route("POST", "/api/normalize")
def api_normalize(req, body):
    return start_job("normalize", lambda p: f"{logic.normalize_pending(p)} varenavn gjenkjent")


@route("GET", r"/api/jobs/(\w+)")
def api_job(req, body, jid):
    j = JOBS.get(jid)
    if not j:
        raise ApiError("Ukjent jobb", 404)
    return j


@route("GET", "/api/shopping")
def api_shopping(req, body):
    sugg, regulars = logic.shopping_suggestions()
    # rullerende beredskap: merk forslag som også er beredskapsvarer, og foreslå å fylle opp det som mangler
    import beredskap
    blinks = beredskap.product_links()
    fmt = lambda b: f"{b['have']:g} av {b['need']:g} {b['unit']}".replace(".", ",")
    for x in sugg:
        b = blinks.get(x["id"])
        if b:
            x["bered"] = f"Også beredskap ({fmt(b)}) – bruk eldste først"
    have_ids = {x["id"] for x in sugg}
    done_keys = {blinks[x["id"]]["key"] for x in sugg if x["id"] in blinks}
    stats_by_id = {x["id"]: x for x in logic.product_stats()}
    by_key = {}
    for pid, b in blinks.items():
        if b["status"] in ("missing", "part") and b["key"] not in done_keys and pid in stats_by_id:
            cur = by_key.get(b["key"])
            if cur is None or stats_by_id[pid]["since"] < stats_by_id[cur]["since"]:
                by_key[b["key"]] = pid
    for key, pid in by_key.items():
        if pid in have_ids or stats_by_id[pid]["ignore"]:
            continue
        b = blinks[pid]
        sugg.append({**stats_by_id[pid], "reason": f"Fyll opp beredskapen: har {fmt(b)}", "score": 0.3,
                     "due_in": 99, "bered": f"Beredskap: {b['name']}", "bered_fill": True})
    todo = db.get_setting("todo_entity")
    current, err = [], None
    if todo:
        try:
            current = ha.todo_items(todo)
        except Exception as e:
            err = str(e)
    # varer som allerede står på handlelista, skjules – og dukker opp igjen når de fjernes fra lista
    on_list = logic.on_list_matcher(current)
    hidden = [s["name"] for s in sugg if on_list(s["name"])]
    sugg = [s for s in sugg if not on_list(s["name"])]
    regulars = [s for s in regulars if not on_list(s["name"])]
    plan = db.get_setting("last_plan") or {}
    missing = []
    for dag in plan.get("dager", []):
        for f in dag.get("forslag", []):
            if f.get("valgt") or f.get("planlagt"):
                for m in f.get("mangler", []):
                    if not on_list(m) and m not in missing:
                        missing.append(m)
    return {
        "suggestions": sugg,
        "hidden_on_list": hidden,
        "regulars": regulars[:60],
        "current": current,
        "todo_entity": todo,
        "todo_error": err,
        "from_plan": missing,
    }


@route("POST", "/api/shopping/remove")
def api_shopping_remove(req, body):
    """Fjerner varer helt fra handlelista i Home Assistant."""
    todo = db.get_setting("todo_entity")
    if not todo:
        raise ApiError("Velg handleliste under Innstillinger først")
    items = [i for i in (body or {}).get("items", []) if i]
    if body.get("all"):
        items = ha.todo_items(todo)
    if not items:
        return {"removed": []}
    removed = []
    for it in items:  # én og én, så én ukjent vare ikke stopper resten
        try:
            ha.todo_remove(todo, it)
            removed.append(it)
        except Exception:
            pass
    return {"removed": removed}


@route("POST", "/api/shopping/dismiss")
def api_shopping_dismiss(req, body):
    pid = int(body.get("product_id") or 0)
    if not pid:
        raise ApiError("Mangler vare")
    if body.get("mode") == "never":
        db.ex("UPDATE products SET ignore=1 WHERE id=?", (pid,))
    else:
        logic.snooze_product(pid)
    return {"ok": True}


@route("POST", "/api/shopping/add")
def api_shopping_add(req, body):
    todo = db.get_setting("todo_entity")
    if not todo:
        raise ApiError("Velg handleliste under Innstillinger først")
    items = [i.strip() for i in (body or {}).get("items", []) if i and i.strip()]
    try:
        existing = {c.strip().lower() for c in ha.todo_items(todo)}
    except Exception:
        existing = set()
    added = []
    for it in items:
        if it.lower() in existing:
            continue
        ha.todo_add(todo, it)
        existing.add(it.lower())
        added.append(it)
    return {"added": added}


@route("GET", "/api/economy")
def api_economy(req, body):
    import economy
    return economy.compute()


@route("GET", "/api/economy/summary")
def api_economy_summary(req, body):
    import economy
    return economy.summary()


@route("POST", "/api/economy/budget")
def api_economy_budget(req, body):
    import economy
    try:
        b = max(0, int(float(body.get("budget") or 0)))
    except (TypeError, ValueError):
        raise ApiError("Ugyldig beløp")
    db.set_setting("budget_month", b)
    threading.Thread(target=economy.push_sensor, daemon=True).start()
    return {"budget": b}


@route("GET", "/api/beredskap")
def api_beredskap(req, body):
    import beredskap
    return beredskap.compute()


@route("POST", "/api/beredskap/household")
def api_beredskap_household(req, body):
    import beredskap
    beredskap.set_household(body)
    return beredskap.compute()


@route("POST", "/api/beredskap/item")
def api_beredskap_item(req, body):
    import beredskap
    try:
        beredskap.set_item(body.get("key"), body)
    except KeyError:
        raise ApiError("Ukjent vare")
    return beredskap.compute()


@route("POST", "/api/beredskap/custom")
def api_beredskap_custom(req, body):
    import beredskap
    if body.get("remove"):
        beredskap.remove_custom(body["remove"])
    else:
        name = (body.get("name") or "").strip()
        if not name:
            raise ApiError("Skriv hva du vil legge til")
        beredskap.add_custom(name, body.get("cat"), body.get("need"), body.get("unit"))
    return beredskap.compute()


@route("POST", "/api/beredskap/tolist")
def api_beredskap_tolist(req, body):
    """Legger det som mangler i beredskapslageret på handlelista i Home Assistant."""
    import beredskap
    todo = db.get_setting("todo_entity")
    if not todo:
        raise ApiError("Velg handleliste under Mer → Innstillinger først")
    keys = set(body.get("keys") or [])
    data = beredskap.compute()
    existing = {c.strip().lower() for c in ha.todo_items(todo)}
    added = []
    for i in data["missing"]:
        if keys and i["key"] not in keys:
            continue
        txt = beredskap.shopping_text(i)
        if txt.lower() in existing:
            continue
        ha.todo_add(todo, txt)
        added.append(txt)
    return {"added": added}


@route("GET", "/api/pantry")
def api_pantry(req, body):
    stats = logic.product_stats()
    stats.sort(key=lambda s: (s["category"], s["name"]))
    import beredskap
    blinks = beredskap.product_links()
    for x in stats:
        b = blinks.get(x["id"])
        if b:
            x["bered"] = b
    return stats


# --- Oda: kun legge varer i handlekurven, aldri bestille ---------------------

@route("GET", "/api/oda/status")
def api_oda_status(req, body):
    import oda
    return {"configured": oda.client.configured(), "last": db.get_setting("oda_last")}


@route("POST", "/api/oda/match")
def api_oda_match(req, body):
    import odamatch
    names = [n.strip() for n in (body or {}).get("items", []) if n and n.strip()]
    if not names:
        raise ApiError("Ingen varer")
    return start_job("oda-match", lambda p: odamatch.match(names[:60], p))


@route("GET", "/api/oda/search")
def api_oda_search(req, body):
    import oda
    q = (req.query.get("q") or "").strip()
    if not q:
        return []
    try:
        return oda.client.search(q, limit=10)
    except oda.OdaError as e:
        raise ApiError(str(e), 502)


@route("POST", "/api/oda/add")
def api_oda_add(req, body):
    """Legger varer i Oda-handlekurven. Bestiller INGENTING."""
    import oda
    import odamatch
    items = [i for i in (body or {}).get("items", []) if i.get("product_id")]
    if not items:
        raise ApiError("Ingen varer valgt")
    try:
        cart = oda.client.add_to_cart([{"product_id": i["product_id"], "quantity": i.get("quantity") or 1} for i in items])
    except oda.OdaError as e:
        raise ApiError(str(e), 502)
    for i in items:
        if i.get("name"):
            odamatch.remember(i["name"], i["product_id"], i.get("oda_name"))
    done = []
    todo = db.get_setting("todo_entity")
    if body.get("complete_ha") and todo:
        for i in items:
            if i.get("from_list"):
                try:
                    ha.call_service("todo", "update_item", {"entity_id": todo, "item": i["from_list"], "status": "completed"})
                    done.append(i["from_list"])
                except Exception:
                    pass
    db.set_setting("oda_last", {"at": logic.now().isoformat(timespec="minutes"), "added": len(items),
                                "by": req.user.get("name")})
    return {"cart": cart, "added": len(items), "completed_in_ha": done}


# --- Priser: dagens Oda-pris/tilbud og sammenligning via Kassalapp ----------

@route("GET", "/api/prices/status")
def api_prices_status(req, body):
    import kassal
    import oda
    return {"oda": oda.client.configured(), "kassal": kassal.configured()}


def _usual_prices():
    return {s["name"].lower(): s for s in logic.product_stats()}


@route("POST", "/api/prices/oda")
def api_prices_oda(req, body):
    import oda
    import odamatch
    names = list(dict.fromkeys(n.strip() for n in (body or {}).get("items", []) if n and n.strip()))[:60]

    def work(progress):
        try:
            odamatch.refresh_history(progress=progress)
        except oda.OdaError:
            pass
        hist = odamatch._history()
        out = {}
        for i, n in enumerate(names):
            progress(f"Henter dagens Oda-pris ({i + 1}/{len(names)})")
            try:
                prod, how, _ = odamatch.best_for(n, hist)
                if prod and prod.get("id"):
                    p = prod if prod.get("price") is not None else odamatch.price_for(prod["id"], prod.get("name"))
                    if p:
                        out[n] = {**p, "how": how}
            except oda.OdaError as e:
                out[n] = {"error": str(e)}
                break
        return out
    return start_job("oda-prices", work)


@route("POST", "/api/compare")
def api_compare(req, body):
    import kassal
    import odamatch
    names = list(dict.fromkeys(n.strip() for n in (body or {}).get("items", []) if n and n.strip()))[:100]
    if not names:
        raise ApiError("Ingen varer å sammenligne")

    def work(progress):
        kassal.on_wait = progress
        usual = _usual_prices()
        hist = odamatch._history() if db.one("SELECT name FROM sqlite_master WHERE name='oda_history'") else []
        rows = []
        for n in names:
            hints = []
            try:
                prod, _, _ = odamatch.best_for(n, hist, allow_search=False)
                if prod and prod.get("name"):
                    hints.append(prod["name"])
            except Exception:
                pass
            # navn fra egne kvitteringer (med merke og størrelse) som ekstra hint
            p = db.one("SELECT id FROM products WHERE name=?", (n,))
            if p:
                hints += [x["raw_name"] for x in db.q(
                    "SELECT raw_name, COUNT(*) c FROM items WHERE product_id=? GROUP BY raw_key ORDER BY c DESC LIMIT 2",
                    (p["id"],)) if x["raw_name"] and x["raw_name"].lower() != n.lower()]
            u = usual.get(n.lower())
            pr = (u or {}).get("prices") or {}
            rows.append({"name": n, "hints": hints[:3], "category": (u or {}).get("category"),
                         "usual": u["avg_price"] if u else None,
                         "usual_unit": pr.get("avg90") if pr.get("unit") in ("kg", "l", "stk") else None,
                         "basis": pr.get("unit")})
        rows, prices = kassal.choose(rows, progress)
        try:
            return kassal.compare(rows, prices)
        finally:
            kassal.on_wait = None
    return start_job("compare", work)


@route("GET", "/api/kassal/search")
def api_kassal_search(req, body):
    import kassal
    try:
        return kassal.search(req.query.get("q") or "", size=15)
    except kassal.KassalError as e:
        raise ApiError(str(e), 502)


@route("POST", "/api/kassal/remember")
def api_kassal_remember(req, body):
    import kassal
    if not body.get("name") or not body.get("ean"):
        raise ApiError("Mangler vare")
    kassal.remember(body["name"], body["ean"], body.get("kname"))
    return {"ok": True}


# --- Butikkvarsel (automasjon i Home Assistant) -----------------------------

def _matplan_path():
    """Adressen til Matplan-panelet i HA (for lenke i varselet)."""
    import os
    import urllib.request
    try:
        req = urllib.request.Request("http://supervisor/addons/self/info",
                                     headers={"Authorization": "Bearer " + os.environ.get("SUPERVISOR_TOKEN", "")})
        with urllib.request.urlopen(req, timeout=5) as r:
            slug = json.loads(r.read().decode()).get("data", {}).get("slug")
            return f"/hassio/ingress/{slug}" if slug else None
    except Exception:
        return None


@route("GET", "/api/storealert")
def api_storealert(req, body):
    import storealert
    s = storealert.settings()
    persons, notifies = storealert.entities()
    if not s.get("people") and persons:
        s["people"] = storealert.guess_people(persons, notifies)
    try:
        st = storealert.status()
    except Exception:
        st = {"installed": False, "on": False}
    todo = db.get_setting("todo_entity")
    preview = storealert.build(s, todo or "todo.handleliste") if s.get("people") else None
    return {"settings": s, "persons": persons, "notifies": notifies, "status": st,
            "todo_entity": todo, "preview": preview}


@route("POST", "/api/storealert")
def api_storealert_save(req, body):
    import storealert
    return {"settings": storealert.save(body or {})}


@route("POST", "/api/storealert/install")
def api_storealert_install(req, body):
    import storealert
    todo = db.get_setting("todo_entity")
    if not todo:
        raise ApiError("Velg handleliste under Innstillinger først")
    try:
        return storealert.install(todo, _matplan_path(), turn_on=bool((body or {}).get("on")))
    except RuntimeError as e:
        raise ApiError(str(e))


@route("POST", "/api/storealert/toggle")
def api_storealert_toggle(req, body):
    import storealert
    try:
        return storealert.set_on(bool((body or {}).get("on")))
    except RuntimeError as e:
        raise ApiError(str(e))


@route("GET", "/api/vocab")
def api_vocab(req, body):
    """Ordliste til autofullføring: egne varer først (mest kjøpt øverst), så appens ordliste."""
    import categorize
    rows = db.q(
        "SELECT p.name, p.category, COUNT(i.id) AS n FROM products p "
        "LEFT JOIN items i ON i.product_id=p.id GROUP BY p.id ORDER BY n DESC, p.name"
    )
    seen, out = set(), []
    for r in rows:
        k = r["name"].casefold()
        if k not in seen:
            seen.add(k)
            out.append({"w": r["name"], "c": r["category"] or "Annet", "own": 1})
    for cat, kws in categorize.KEYWORDS.items():
        for kw in kws:
            if kw in ("frossen", "frosne", "frys", "frosen"):
                continue
            w = categorize.pretty_name(kw)
            if w.casefold() not in seen:
                seen.add(w.casefold())
                out.append({"w": w, "c": cat, "own": 0})
    return out


@route("GET", "/api/categorize")
def api_categorize(req, body):
    names = [n for n in (req.query.get("names") or req.query.get("name") or "").split(",") if n.strip()]
    return [logic.suggest_category(n) for n in names[:30]]


@route("POST", "/api/pantry/add")
def api_pantry_add(req, body):
    items = (body or {}).get("items") or []
    if not items:
        raise ApiError("Ingen varer")
    return {"added": logic.add_manual(items, bool(body.get("bought", True)), req.user.get("name"))}


@route("POST", "/api/pantry")
def api_pantry_set(req, body):
    logic.set_pantry(int(body["product_id"]), body.get("status"))
    return {"ok": True}


@route("POST", r"/api/products/(\d+)")
def api_product(req, body, pid):
    if "ignore" in body:
        db.ex("UPDATE products SET ignore=? WHERE id=?", (1 if body["ignore"] else 0, pid))
    if body.get("category"):
        db.ex("UPDATE products SET category=? WHERE id=?", (body["category"], pid))
    if body.get("shelf_days"):
        db.ex("UPDATE products SET shelf_days=? WHERE id=?", (int(body["shelf_days"]), pid))
    if body.get("name"):
        new = body["name"].strip()
        other = db.one("SELECT id FROM products WHERE name=? AND id!=?", (new, pid))
        if other:  # slå sammen med eksisterende produkt
            db.ex("UPDATE items SET product_id=? WHERE product_id=?", (other["id"], pid))
            db.ex("UPDATE name_map SET product_id=? WHERE product_id=?", (other["id"], pid))
            db.ex("DELETE FROM pantry WHERE product_id=?", (pid,))
            db.ex("DELETE FROM products WHERE id=?", (pid,))
        else:
            db.ex("UPDATE products SET name=? WHERE id=?", (new, pid))
    return {"ok": True}


@route("GET", "/api/dinner")
def api_dinner(req, body):
    return db.get_setting("last_plan") or {}


@route("POST", "/api/dinner")
def api_dinner_new(req, body):
    days = (body or {}).get("days", 3)
    wish = (body or {}).get("wish", "")
    return start_job("dinner", lambda p: logic.dinner_plan(days, wish, p))


@route("POST", "/api/dinner/choose")
def api_dinner_choose(req, body):
    """Velg et forslag. Skrives inn i middagskalenderen hvis det er slått på."""
    plan = db.get_setting("last_plan") or {}
    msg = None
    for dag in plan.get("dager", []):
        if dag.get("dato") != body.get("dato"):
            continue
        forslag = dag.get("forslag", [])
        idx = body.get("index")
        if not isinstance(idx, int) or not 0 <= idx < len(forslag):
            raise ApiError("Ukjent forslag")
        f = forslag[idx]
        written = next((x for x in forslag if x.get("i_kalender")), None)
        if f.get("valgt"):
            if f.get("i_kalender"):
                raise ApiError("Står allerede i middagskalenderen – endre eller slett den i kalenderen")
            f["valgt"] = False
            break
        if written:
            raise ApiError(f"«{written['tittel']}» står allerede i middagskalenderen for denne dagen")
        for x in forslag:
            x["valgt"] = False
        f["valgt"] = True
        if db.get_setting("write_calendar") and not dag.get("planlagt"):
            title = logic.short_title(f.get("tittel", ""))
            rec = f.get("oppskrift") or logic.cached_recipe(f.get("tittel", ""))
            try:
                logic.write_dinner(dag["dato"], title, logic.recipe_text(rec, f.get("beskrivelse", "")))
                f["i_kalender"] = True
                dag["planlagt"] = title
                msg = f"«{title}» er lagt inn i middagskalenderen"
            except Exception as e:
                msg = f"Valgt, men ikke lagt i kalenderen: {e}"
        break
    db.set_setting("last_plan", plan)
    return {**plan, "melding": msg}


def _find_suggestion(plan, dato, index):
    dag = next((x for x in plan.get("dager", []) if x.get("dato") == dato), None)
    if not dag or not isinstance(index, int) or not 0 <= index < len(dag.get("forslag", [])):
        raise ApiError("Fant ikke forslaget")
    return dag, dag["forslag"][index]


@route("POST", "/api/dinner/edit")
def api_dinner_edit(req, body):
    """Rediger et forslag (tittel, beskrivelse, ingredienser, oppskrift)."""
    plan = db.get_setting("last_plan") or {}
    dag, f = _find_suggestion(plan, body.get("dato"), body.get("index"))
    old_title = f.get("tittel", "")
    for k in ("tittel", "beskrivelse", "type"):
        if k in body and isinstance(body[k], str):
            f[k] = body[k].strip()
    if "min" in body:
        try:
            f["min"] = int(body["min"]) if body["min"] not in ("", None) else None
        except (TypeError, ValueError):
            pass
    for k in ("bruker", "mangler"):
        if isinstance(body.get(k), list):
            f[k] = [x.strip() for x in body[k] if str(x).strip()]
    if isinstance(body.get("oppskrift"), dict):
        f["oppskrift"] = body["oppskrift"]
        logic.save_recipe(f["tittel"], body["oppskrift"], edited=True)
    elif f.get("tittel") != old_title:
        # ny rett: bruk lagret oppskrift for den hvis vi har en
        f.pop("oppskrift", None)
        rec = logic.cached_recipe(f["tittel"])
        if rec:
            f["oppskrift"] = rec
    db.set_setting("last_plan", plan)
    msg = "Står allerede i kalenderen – endringen oppdaterer ikke kalenderavtalen" if f.get("i_kalender") else None
    return {**plan, "melding": msg}


@route("POST", "/api/dinner/custom")
def api_dinner_custom(req, body):
    """Legg inn en egen middag på en dag."""
    plan = db.get_setting("last_plan") or {}
    dag = next((x for x in plan.get("dager", []) if x.get("dato") == body.get("dato")), None)
    title = (body.get("tittel") or "").strip()
    if not dag or not title:
        raise ApiError("Mangler dag eller tittel")
    f = {"tittel": title, "beskrivelse": (body.get("beskrivelse") or "").strip(), "egen": True,
         "bruker": [], "mangler": []}
    rec = logic.cached_recipe(title)
    if rec:
        f["oppskrift"] = rec
    dag.setdefault("forslag", []).append(f)
    db.set_setting("last_plan", plan)
    return plan


@route("POST", "/api/dinner/replace")
def api_dinner_replace(req, body):
    dato, index = body.get("dato"), body.get("index")
    return start_job("replace", lambda p: logic.replace_suggestion(dato, index, p))


@route("POST", "/api/recipe")
def api_recipe(req, body):
    """Hent (eller lag) oppskrift. Med dato/index lagres den også på forslaget."""
    title = (body.get("title") or "").strip()
    if not title:
        raise ApiError("Mangler tittel")
    dato, index = body.get("dato"), body.get("index")

    def work(progress):
        progress("Claude skriver oppskrift")
        people = None
        plan = db.get_setting("last_plan") or {}
        if dato is not None:
            dag = next((x for x in plan.get("dager", []) if x.get("dato") == dato), None)
            people = (dag or {}).get("hjemme")
        rec = logic.get_recipe(title, body.get("description", ""), people, bool(body.get("force")))
        if dato is not None and isinstance(index, int):
            plan = db.get_setting("last_plan") or {}
            try:
                _, f = _find_suggestion(plan, dato, index)
                f["oppskrift"] = rec
                db.set_setting("last_plan", plan)
            except ApiError:
                pass
        return rec

    cached = None if body.get("force") else logic.cached_recipe(title)
    if cached:
        return {"id": None, "status": "done", "result": cached}
    return start_job("recipe", work)


@route("POST", "/api/recipe/save")
def api_recipe_save(req, body):
    title = (body.get("title") or "").strip()
    rec = body.get("recipe")
    if not title or not isinstance(rec, dict):
        raise ApiError("Mangler tittel eller oppskrift")
    logic.save_recipe(title, rec, edited=True)
    return {"ok": True}


@route("GET", "/api/dishes")
def api_dishes(req, body):
    return logic.dish_stats()


@route("POST", "/api/dishes/rate")
def api_dish_rate(req, body):
    title = (body.get("title") or "").strip()
    if not title:
        raise ApiError("Mangler tittel")
    logic.rate_dish(title, body.get("rating") or 0)
    return {"ok": True}


@route("POST", "/api/calendar/sync")
def api_calendar_sync(req, body):
    def work(progress):
        progress("Leser middagskalenderen")
        planned = logic.sync_dinner_calendar()
        info = db.get_setting("calendar_sync") or {}
        return f"Hentet {info.get('meals', 0)} tidligere middager, {len(planned)} planlagt framover"
    return start_job("calendar", work)


@route("GET", "/api/meals")
def api_meals(req, body):
    ratings = {r["title_key"]: r["rating"] for r in db.q("SELECT * FROM dish_ratings")}
    rows = db.q(
        "SELECT id, day, title, title_key, by_user, source FROM meals WHERE day<=? "
        "ORDER BY day DESC, id DESC LIMIT 60",
        (logic.today().isoformat(),),
    )
    for r in rows:
        r["rating"] = ratings.get(r["title_key"] or logic.title_key(r["title"]), 0)
    return rows


@route("POST", "/api/meals")
def api_meal_add(req, body):
    title = (body.get("title") or "").strip()
    if not title:
        raise ApiError("Mangler tittel")
    day = body.get("day") or logic.today().isoformat()
    key = logic.title_key(title)
    existing = db.one("SELECT id FROM meals WHERE day=? AND title_key=?", (day, key))
    if existing:
        mid = existing["id"]
        db.ex("UPDATE meals SET source='app', by_user=? WHERE id=?", (req.user.get("name"), mid))
    else:
        mid = db.ex(
            "INSERT INTO meals(day, title, title_key, by_user, source, data) VALUES(?,?,?,?, 'app', ?)",
            (day, title, key, req.user.get("name"), json.dumps(body.get("data") or {}, ensure_ascii=False)),
        )
    if body.get("rating"):
        logic.rate_dish(title, body["rating"])
    used = logic.mark_used(body.get("used") or [])
    return {"id": mid, "marked_used": used}


@route("POST", r"/api/meals/(\d+)")
def api_meal_rate(req, body, mid):
    m = db.one("SELECT title FROM meals WHERE id=?", (mid,))
    if not m:
        raise ApiError("Fant ikke middagen", 404)
    logic.rate_dish(m["title"], body.get("rating") or 0)
    return {"ok": True}


@route("DELETE", r"/api/meals/(\d+)")
def api_meal_delete(req, body, mid):
    db.ex("DELETE FROM meals WHERE id=?", (mid,))
    return {"ok": True}


# --- HTTP-håndtering --------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "Matplan"

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _allowed(self):
        return DEV or self.client_address[0] in (INGRESS_IP, "127.0.0.1")

    @property
    def query(self):
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    @property
    def user(self):
        return {
            "id": self.headers.get("X-Remote-User-Id"),
            "name": self.headers.get("X-Remote-User-Display-Name")
            or self.headers.get("X-Remote-User-Name"),
        }

    def _send(self, code, payload, ctype="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC, rel))
        if not full.startswith(os.path.realpath(STATIC)) or not os.path.isfile(full):
            full = os.path.join(STATIC, "index.html")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def _handle(self, method):
        if not self._allowed():
            return self._send(403, {"error": "Kun tilgjengelig via Home Assistant"})
        path = unquote(urlparse(self.path).path)
        # ingress kan sende hele stien; vi bryr oss bare om delen etter /api
        api_idx = path.find("/api/")
        if api_idx >= 0:
            path = path[api_idx:]
            body = None
            if method in ("POST", "PUT"):
                n = int(self.headers.get("Content-Length") or 0)
                if n > 60 * 1024 * 1024:
                    return self._send(413, {"error": "For stor opplasting"})
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    return self._send(400, {"error": "Ugyldig JSON"})
            for m, rx, fn in ROUTES:
                if m != method:
                    continue
                mt = rx.match(path)
                if mt:
                    try:
                        return self._send(200, fn(self, body, *mt.groups()))
                    except ApiError as e:
                        return self._send(e.code, {"error": str(e)})
                    except ha.HAError as e:
                        return self._send(502, {"error": str(e)})
                    except Exception as e:
                        log.error("Feil i %s %s: %s\n%s", method, path, e, traceback.format_exc())
                        return self._send(500, {"error": str(e)})
            return self._send(404, {"error": "Ukjent adresse"})
        if method == "GET":
            return self._static(path)
        return self._send(405, {"error": "Ikke tillatt"})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")


def run():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    log.info("Matplan lytter på port %s", PORT)
    srv.serve_forever()
