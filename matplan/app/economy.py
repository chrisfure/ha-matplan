"""Økonomi: hva dere bruker på mat og dagligvarer, regnet ut fra kvitteringene (uten AI).

- Snittet regnes bare av hele måneder: måneden som pågår og den første måneden med
  kvitteringer (som trolig ikke er komplett) holdes utenfor.
- Måneder med uvanlig få kvitteringer merkes som «trolig ufullstendig» og holdes også utenfor.
- Mat og annet skilles på varens kategori (husholdning og hygiene er «annet»).
"""
import calendar
import logging
from collections import defaultdict
from datetime import date
from statistics import median

import db
import ha
import logic

log = logging.getLogger("matplan.economy")

NON_FOOD = {"Husholdning", "Hygiene"}
MONTHS_NB = ["jan", "feb", "mar", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "des"]
SENSOR = "sensor.matplan_dagligvarer_denne_maned"


def _ym(d):
    return d[:7]


def _month_list(first, last):
    y, m = int(first[:4]), int(first[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _is_food(row):
    if not row["pid"]:
        return None  # pant, rabatter o.l. – verken mat eller annet
    if row["category"] in NON_FOOD or not row["food"]:
        return False
    return True


def budget():
    try:
        return int(db.get_setting("budget_month") or 0)
    except (TypeError, ValueError):
        return 0


def compute(window=6):
    today = logic.today()
    cur = today.strftime("%Y-%m")
    receipts = db.q(
        "SELECT id, store, purchased_on AS d, total FROM receipts "
        "WHERE status='ok' AND source != 'manual' AND purchased_on IS NOT NULL ORDER BY purchased_on"
    )
    lines = db.q(
        "SELECT i.receipt_id AS rid, COALESCE(i.price,0) AS price, i.product_id AS pid, "
        "p.category, p.food, p.name FROM items i LEFT JOIN products p ON p.id=i.product_id"
    )
    by_rec = defaultdict(list)
    for ln in lines:
        by_rec[ln["rid"]].append(ln)

    months = defaultdict(lambda: {"total": 0.0, "food": 0.0, "other": 0.0, "count": 0, "stores": defaultdict(float)})
    cats = defaultdict(float)
    cut90 = date.fromordinal(today.toordinal() - 90).isoformat()
    for r in receipts:
        items = by_rec.get(r["id"], [])
        item_sum = sum(x["price"] for x in items)
        total = r["total"] if r["total"] else item_sum
        food = sum(x["price"] for x in items if _is_food(x) is True)
        other = sum(x["price"] for x in items if _is_food(x) is False)
        # rest (pant, rabatt, avrunding, linjer som ikke ble lest) fordeles forholdsmessig
        rest = total - food - other
        if food + other > 0:
            food += rest * food / (food + other)
            other = total - food
        else:
            food = total
        m = months[_ym(r["d"])]
        m["total"] += total
        m["food"] += food
        m["other"] += other
        m["count"] += 1
        m["stores"][r["store"] or "Ukjent"] += total
        if r["d"] >= cut90:
            for x in items:
                if x["pid"] and x["price"] > 0:
                    cats[x["category"] or "Annet"] += x["price"]

    if not receipts:
        return {"empty": True, "budget": budget()}

    first = _ym(receipts[0]["d"])
    all_months = _month_list(first, cur)
    full = [m for m in all_months if m != cur and m != first]
    counts = [months[m]["count"] for m in full if months[m]["count"]]
    med = median(counts) if counts else 0
    rows = []
    for ym in all_months[-13:]:
        m = months[ym]
        incomplete = ym == first or (ym != cur and med >= 4 and m["count"] < med * 0.5)
        rows.append({
            "month": ym,
            "label": MONTHS_NB[int(ym[5:7]) - 1],
            "year": int(ym[:4]),
            "total": round(m["total"]), "food": round(m["food"]), "other": round(m["other"]),
            "count": m["count"],
            "current": ym == cur,
            "incomplete": incomplete,
        })
    basis = [r for r in rows if not r["current"] and not r["incomplete"]][-window:]
    n = len(basis)
    avg = sum(r["total"] for r in basis) / n if n else None
    avg_food = sum(r["food"] for r in basis) / n if n else None
    trips = sum(r["count"] for r in basis) / n if n else None

    # denne måneden og anslag: brukt så langt + snitt per dag for resten av måneden
    m_cur = months[cur]
    dim = calendar.monthrange(today.year, today.month)[1]
    remaining = dim - today.day
    if avg:
        projection = m_cur["total"] + avg / dim * remaining
    elif today.day >= 7:
        projection = m_cur["total"] / today.day * dim
    else:
        projection = None

    # butikker: snitt per måned i grunnlagsmånedene
    stores = defaultdict(lambda: {"sum": 0.0, "count": 0})
    basis_set = {r["month"] for r in basis} or {r["month"] for r in rows if not r["current"]}
    for r in receipts:
        if _ym(r["d"]) in basis_set:
            items = by_rec.get(r["id"], [])
            s = stores[r["store"] or "Ukjent"]
            s["sum"] += r["total"] if r["total"] else sum(x["price"] for x in items)
            s["count"] += 1
    nb = max(1, len(basis_set))
    tot_st = sum(s["sum"] for s in stores.values()) or 1
    store_rows = sorted(({
        "store": k, "per_month": round(v["sum"] / nb), "share": round(100 * v["sum"] / tot_st),
        "per_trip": round(v["sum"] / v["count"]) if v["count"] else None,
        "trips_per_month": round(v["count"] / nb, 1),
    } for k, v in stores.items()), key=lambda x: -x["per_month"])

    tot_cat = sum(cats.values()) or 1
    cat_rows = sorted(({"category": k, "sum": round(v), "share": round(100 * v / tot_cat)} for k, v in cats.items()),
                      key=lambda x: -x["sum"])

    # varer: mest penger, dyrere og mulig sparing (fra prisberegningene i lageret)
    stats = logic.product_stats()
    top = sorted((s for s in stats if s.get("spent_90")), key=lambda s: -s["spent_90"])[:10]
    pricier, savings = [], []
    for s in stats:
        pr = s.get("prices") or {}
        if not pr or s.get("times", 0) < 3:
            continue
        if pr.get("trend") is not None and pr["trend"] >= 8 and s.get("q90", 0) >= 1:
            pricier.append({"name": s["name"], "trend": pr["trend"], "last": pr.get("last"), "avg": pr.get("avg90"),
                            "unit": pr.get("unit")})
        mn, a = pr.get("min"), pr.get("avg90")
        if mn and a and mn.get("price") and mn["price"] <= a * 0.88 and pr.get("spent90"):
            pct = round(100 * (1 - mn["price"] / a))
            savings.append({
                "name": s["name"], "pct": pct, "unit": pr.get("unit"),
                "best": mn["price"], "avg": a, "store": mn.get("store"), "size": mn.get("size"),
                "pack": mn.get("pack"),
                "per_month": round(pr["spent90"] / 3 * pct / 100),
            })
    pricier.sort(key=lambda x: -x["trend"])

    # helse: andel av matpengene som går til ultraprosessert mat (siste 3 mnd)
    import health
    health.annotate(stats)
    food90 = [s for s in stats if s.get("spent_90") and s.get("food", True) and s["category"] not in NON_FOOD]
    tot90 = sum(s["spent_90"] for s in food90) or 0
    upf90 = [s for s in food90 if (s.get("health") or {}).get("upf")]
    meat90 = [s for s in food90 if "meat" in ((s.get("health") or {}).get("tags") or [])]
    health_info = {
        "food_spent": round(tot90),
        "upf_spent": round(sum(s["spent_90"] for s in upf90)),
        "upf_share": round(100 * sum(s["spent_90"] for s in upf90) / tot90) if tot90 else 0,
        "meat_spent": round(sum(s["spent_90"] for s in meat90)),
        "top": [{"id": s["id"], "name": s["name"], "spent": s["spent_90"], "labels": s["health"]["labels"],
                 "alt": s["health"]["alt"]} for s in sorted(upf90 + [m for m in meat90 if m not in upf90], key=lambda s: -s["spent_90"])[:8]],
    }
    savings.sort(key=lambda x: -x["per_month"])

    b = budget()
    return {
        "empty": False,
        "today": today.isoformat(),
        "avg_month": round(avg) if avg else None,
        "avg_food": round(avg_food) if avg_food else None,
        "avg_other": round(avg - avg_food) if avg else None,
        "avg_week": round(avg * 12 / 52) if avg else None,
        "per_trip": round(avg / trips) if avg and trips else None,
        "trips_month": round(trips) if trips else None,
        "basis_months": n,
        "basis_from": basis[0]["month"] if basis else None,
        "current": {
            "month": cur, "so_far": round(m_cur["total"]), "food": round(m_cur["food"]),
            "count": m_cur["count"], "day": today.day, "days": dim,
            "projection": round(projection) if projection else None,
        },
        "months": rows,
        "stores": store_rows,
        "categories": cat_rows,
        "top": [{"name": s["name"], "spent": s["spent_90"], "category": s["category"],
                 "trend": (s.get("prices") or {}).get("trend")} for s in top],
        "pricier": pricier[:8],
        "savings": savings[:8],
        "health": health_info,
        "budget": b,
        "budget_left": b - round(m_cur["total"]) if b else None,
        "sensor": SENSOR,
    }


def summary():
    """Det kvitteringssiden trenger: snitt og denne måneden."""
    e = compute()
    if e.get("empty"):
        return e
    return {k: e[k] for k in ("avg_month", "basis_months", "current", "budget", "budget_left")} | {
        "months": {r["month"]: {"total": r["total"], "incomplete": r["incomplete"]} for r in e["months"]}}


def push_sensor():
    """Oppdaterer en sensor i Home Assistant med hva som er brukt denne måneden."""
    try:
        e = compute()
        if e.get("empty"):
            return
        c = e["current"]
        attrs = {
            "friendly_name": "Dagligvarer denne måneden",
            "unit_of_measurement": "kr",
            "icon": "mdi:cart-outline",
            "state_class": "total",
            "snitt_per_maned": e["avg_month"],
            "anslag_hele_maneden": c["projection"],
            "mat": c["food"],
            "kvitteringer": c["count"],
            "budsjett": e["budget"] or None,
            "igjen_av_budsjett": e["budget_left"],
        }
        ha._req("POST", f"/states/{SENSOR}", {"state": c["so_far"], "attributes": attrs})
    except Exception as ex:  # HA kan være nede – prøv igjen neste gang
        log.warning("Kunne ikke oppdatere sensoren: %s", ex)
