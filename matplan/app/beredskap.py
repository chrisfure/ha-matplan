"""Beredskapslager etter DSBs råd om egenberedskap (klare seg selv i 7 døgn),
skalert etter husstanden, med noen ekstra tips. Alt regnes ut i appen, uten AI.

Kilde: DSB – sikkerhverdag.no/din-beredskap (20 liter vann per person, mat som tåler
romtemperatur, varme, lys, radio, medisiner, jodtabletter, hygiene, kontanter m.m.).
"""
import math
import re
from datetime import date, timedelta

import db
import logic

DEFAULT_HOUSEHOLD = {"adults": 2, "children": 3, "under40": 2, "diaper_kids": 0, "days": 7}

CATS = ["Vann", "Mat", "Varme og matlaging", "Lys, strøm og radio", "Medisin og førstehjelp",
        "Hygiene", "Penger og papirer", "Barn", "Nyttig å ha"]

# Energibehov per døgn (omtrentlig): voksen ~2200 kcal, barn ~1600 kcal
KCAL_ADULT, KCAL_CHILD = 2200, 1600

# Felter: key, cat, name, unit, a (per voksen per uke), c (per barn per uke), f (fast antall),
# scale (skaleres med antall døgn), kind (qty/check), src (DSB/tips), note, kcal (per enhet), keep (holdbarhet, mnd)
ITEMS = [
    # --- Vann
    dict(key="vann", cat="Vann", name="Drikkevann på kanner eller flasker", unit="liter", a=20, c=20, scale=True, src="DSB",
         note="20 liter per person for en uke – til drikke, enkel matlaging og hygiene. Tappevann på rene kanner holder seg lenge; mørkt og kjølig."),
    dict(key="vanndunk", cat="Vann", name="Tomme kanner til å hente vann", unit="stk", f=2, src="tips",
         note="Hvis kommunen setter opp vannposter."),
    dict(key="vannrens", cat="Vann", name="Vannrensetabletter", unit="pakke", f=1, src="tips", keep=60,
         note="Reserve hvis dere må bruke vann av usikker kvalitet."),
    # --- Mat (mengder per person per uke; barn ~70 %)
    dict(key="knekkebrod", cat="Mat", name="Knekkebrød", unit="pakke", a=1, c=0.7, scale=True, src="DSB", kcal=1900, keep=12),
    dict(key="havregryn", cat="Mat", name="Havregryn eller müsli", unit="kg", a=0.5, c=0.4, scale=True, src="tips", kcal=3700, keep=12,
         note="Kan spises kaldt med UHT-melk."),
    dict(key="pasta", cat="Mat", name="Pasta, ris eller couscous", unit="kg", a=1, c=0.7, scale=True, src="DSB", kcal=3550, keep=24,
         note="Krever vann og kokeapparat. Couscous trenger bare kokende vann."),
    dict(key="hermfisk", cat="Mat", name="Hermetisk fisk (makrell, sardiner, tunfisk)", unit="boks", a=3, c=2, scale=True, src="DSB", kcal=230, keep=36),
    dict(key="hermmiddag", cat="Mat", name="Hermetisk middag (lapskaus, gryte, ravioli)", unit="boks", a=3, c=2, scale=True, src="DSB", kcal=500, keep=36,
         note="Kan spises kald om nødvendig."),
    dict(key="bonner", cat="Mat", name="Bønner, linser eller kikerter på boks", unit="boks", a=2, c=1, scale=True, src="tips", kcal=300, keep=36),
    dict(key="hermgront", cat="Mat", name="Hermetisk frukt og grønnsaker", unit="boks", a=2, c=2, scale=True, src="tips", kcal=150, keep=24),
    dict(key="uht", cat="Mat", name="UHT-melk eller plantedrikk", unit="liter", a=2, c=3, scale=True, src="tips", kcal=500, keep=4,
         note="Holder seg noen måneder uåpnet – byttes jevnlig."),
    dict(key="palegg", cat="Mat", name="Pålegg som tåler romtemperatur (peanøttsmør, syltetøy, kaviar)", unit="stk", a=1, c=1, scale=True, src="DSB", kcal=1000, keep=12),
    dict(key="notter", cat="Mat", name="Tørket frukt og nøtter", unit="pose", a=1, c=0.7, scale=True, src="tips", kcal=1100, keep=12,
         note="Mye energi på lite plass. Husk allergier."),
    dict(key="kos", cat="Mat", name="Kjeks, sjokolade og annet kos", unit="pakke", a=2, c=2, scale=True, src="tips", kcal=1200, keep=12,
         note="Godt for humøret – særlig for barna."),
    dict(key="suppe", cat="Mat", name="Posesupper eller turmat", unit="pose", a=2, c=1, scale=True, src="tips", kcal=300, keep=24),
    dict(key="boksapner", cat="Mat", name="Manuell boksåpner", unit="stk", f=1, src="tips", note="Lett å glemme!"),
    # --- Varme og matlaging
    dict(key="kokeapparat", cat="Varme og matlaging", name="Stormkjøkken, primus eller grill", unit="stk", f=1, src="DSB",
         note="Grill og primus brukes ute – aldri innendørs (kullos)."),
    dict(key="gass", cat="Varme og matlaging", name="Brensel til kokeapparatet", unit="boks", f=7, scale=True, src="tips",
         note="Omtrent én gassboks (230 g) per dag for familien."),
    dict(key="dyner", cat="Varme og matlaging", name="Dyner, ulltepper eller soveposer", unit="stk", a=1, c=1, src="DSB"),
    dict(key="ull", cat="Varme og matlaging", name="Varme klær og ullundertøy", unit="", kind="check", src="DSB",
         note="Til alle i familien."),
    dict(key="ved", cat="Varme og matlaging", name="Ved (hvis dere har peis eller vedovn)", unit="sekk", f=7, scale=True, src="DSB",
         note="Sett «ikke aktuelt» hvis dere ikke har ildsted."),
    dict(key="varmeovn", cat="Varme og matlaging", name="Gass- eller parafinovn godkjent for innendørs bruk", unit="stk", f=1, src="DSB",
         note="Alternativ til peis. Sett «ikke aktuelt» hvis dere har annen varmekilde eller avtale om overnatting."),
    # --- Lys, strøm og radio
    dict(key="radio", cat="Lys, strøm og radio", name="DAB-radio på batteri, sveiv eller sol", unit="stk", f=1, src="DSB",
         note="Myndighetene informerer via NRK P1 ved kriser."),
    dict(key="lykt", cat="Lys, strøm og radio", name="Lommelykt eller hodelykt", unit="stk", a=1, c=1, src="DSB"),
    dict(key="batterier", cat="Lys, strøm og radio", name="Batterier som passer lykter og radio", unit="pakke", f=2, src="DSB", keep=60),
    dict(key="lys", cat="Lys, strøm og radio", name="Stearinlys", unit="stk", f=14, scale=True, src="DSB",
         note="Omtrent to per kveld. Aldri ubevoktet."),
    dict(key="fyrstikker", cat="Lys, strøm og radio", name="Fyrstikker eller lighter", unit="stk", f=2, src="DSB"),
    dict(key="powerbank", cat="Lys, strøm og radio", name="Oppladet powerbank", unit="stk", f=2, src="DSB",
         note="Lad dem opp et par ganger i året."),
    # --- Medisin og førstehjelp
    dict(key="resept", cat="Medisin og førstehjelp", name="Ekstra reseptbelagte medisiner", unit="", kind="check", src="DSB",
         note="Minst en ukes forbruk for den som bruker fast medisin."),
    dict(key="forstehjelp", cat="Medisin og førstehjelp", name="Førstehjelpsskrin", unit="stk", f=1, src="DSB",
         note="Sjekk utløpsdatoer jevnlig."),
    dict(key="smerte", cat="Medisin og førstehjelp", name="Smertestillende og febernedsettende for voksne", unit="pakke", f=1, src="tips", keep=36),
    dict(key="smertebarn", cat="Medisin og førstehjelp", name="Febernedsettende for barn (mikstur eller stikkpiller)", unit="pakke", f=1, src="tips", keep=24,
         note="Dosering etter vekt – se pakningen eller spør apoteket."),
    dict(key="jod", cat="Medisin og førstehjelp", name="Jodtabletter", unit="", kind="check", src="DSB", keep=60,
         note="Fra apoteket, til barn og voksne under 40 år. Tas bare når myndighetene ber om det."),
    dict(key="termometer", cat="Medisin og førstehjelp", name="Termometer", unit="stk", f=1, src="tips"),
    # --- Hygiene
    dict(key="dopapir", cat="Hygiene", name="Toalettpapir", unit="rull", a=2, c=2, scale=True, src="DSB"),
    dict(key="vatserv", cat="Hygiene", name="Våtservietter", unit="pakke", a=1, c=1, scale=True, src="DSB"),
    dict(key="antibac", cat="Hygiene", name="Hånddesinfeksjon (antibac)", unit="flaske", f=2, src="DSB"),
    dict(key="nodtoalett", cat="Hygiene", name="Nødtoalett: bøtte med lokk og kraftige poser", unit="", kind="check", src="DSB",
         note="Poser i bøtta eller over toalettskålen; knytes dobbelt og kastes i restavfall."),
    dict(key="hansker", cat="Hygiene", name="Engangshansker", unit="pakke", f=1, src="tips"),
    dict(key="sopp", cat="Hygiene", name="Søppelsekker", unit="rull", f=1, src="tips"),
    dict(key="bind", cat="Hygiene", name="Bind og tamponger", unit="pakke", f=1, src="DSB", note="Sett «ikke aktuelt» om det ikke trengs."),
    dict(key="tallerkener", cat="Hygiene", name="Engangstallerkener og -bestikk", unit="pakke", f=1, src="tips", note="Sparer vann til oppvask."),
    # --- Penger og papirer
    dict(key="kontanter", cat="Penger og papirer", name="Kontanter i små sedler", unit="", kind="check", src="DSB",
         note="Litt kontanter i tillegg til kort, i tilfelle betalingssystemene er nede."),
    dict(key="kort", cat="Penger og papirer", name="Flere betalingskort", unit="", kind="check", src="DSB"),
    dict(key="telefonliste", cat="Penger og papirer", name="Viktige telefonnumre på papir", unit="", kind="check", src="DSB",
         note="Nødnumre, legevakt (116 117), familie, naboer, barnehage og skole."),
    dict(key="dokumenter", cat="Penger og papirer", name="Kopi av viktige dokumenter (pass, forsikring)", unit="", kind="check", src="tips"),
    # --- Barn
    dict(key="bleier", cat="Barn", name="Bleier", unit="stk", d=42, scale=True, src="DSB",
         note="Ca. 6 om dagen per barn som bruker bleie. Antall barn med bleie velges øverst."),
    dict(key="aktiviteter", cat="Barn", name="Aktiviteter uten strøm (kortstokk, spill, bøker, fargeblyanter)", unit="", kind="check", src="tips",
         note="Gjør en lang dag uten strøm mye enklere."),
    dict(key="kosedyr", cat="Barn", name="Kosedyr, nattlys på batteri og favorittsnacks", unit="", kind="check", src="tips"),
    # --- Nyttig å ha
    dict(key="kniv", cat="Nyttig å ha", name="Multiverktøy eller kniv", unit="stk", f=1, src="tips"),
    dict(key="teip", cat="Nyttig å ha", name="Gaffateip og plastposer", unit="", kind="check", src="tips"),
    dict(key="kjolebag", cat="Nyttig å ha", name="Kjølebag med kjøleelementer", unit="stk", f=1, src="tips",
         note="Holder mat fra fryseren kald litt lenger ved strømbrudd."),
    dict(key="brann", cat="Nyttig å ha", name="Brannslukker og testet røykvarsler", unit="", kind="check", src="tips",
         note="Mer levende lys og alternative varmekilder øker brannfaren."),
    dict(key="bil", cat="Nyttig å ha", name="Bilen minst halvfull eller ladet", unit="", kind="check", src="tips"),
    dict(key="kjaeledyr", cat="Nyttig å ha", name="Mat og vann til kjæledyr", unit="", kind="check", src="DSB",
         note="Sett «ikke aktuelt» om dere ikke har dyr."),
]
BY_KEY = {i["key"]: i for i in ITEMS}

# Varer dere bruker i hverdagen kobles til lageret (rullerende beredskap): kjøper dere varen,
# fornyes beredskapen – bruk det eldste først. Mønstrene matcher varenavn i lageret.
LINKS = {
    "knekkebrod": r"knekkebr",
    "havregryn": r"havregryn|müsli|musli|granola|havre",
    "pasta": r"pasta|spagett|makaroni|fusilli|penne|tagliatelle|lasagneplat|\bris\b|jasminris|basmati|couscous|nudler",
    "hermfisk": r"makrell|sardin|tunfisk|fiskeboller|ansjos|brisling",
    "hermmiddag": r"lapskaus|ravioli|gryte på boks|hermetisk middag|betasuppe",
    "bonner": r"bønner|bonner|linser|kikert",
    "hermgront": r"hakkede tomater|tomater på boks|mais|ananas|fersken|hermetisk",
    "uht": r"uht|langtidsholdbar|havredrikk|soyadrikk|mandeldrikk|plantedrikk",
    "palegg": r"peanøtt|syltetøy|kaviar|nugatti|leverpostei på boks",
    "notter": r"nøtter|notter|rosiner|tørket|mandler|cashew|peanøtter",
    "kos": r"kjeks|sjokolade|smågodt|potetgull|chips",
    "suppe": r"suppe|turmat|toro",
    "dopapir": r"toalettpapir|dopapir|toalettrull",
    "vatserv": r"våtserviett|vatserviett",
    "antibac": r"antibac|hånddesinf|handdesinf",
    "batterier": r"batteri",
    "lys": r"stearinlys|telys|kubbelys|lys \d",
    "bleier": r"bleie",
    "sopp": r"søppelsek|soppelsek|avfallssek",
    "hansker": r"engangshansk",
    "gass": r"gassboks|butangass|primusgass",
}
_LINK_RE = {k: re.compile(v, re.I) for k, v in LINKS.items()}


def _purchases():
    """Kjøp per produkt: [(dato, antall, varenavn)] fra kvitteringene."""
    out = {}
    for r in db.q(
        "SELECT i.product_id AS pid, r.purchased_on AS d, COALESCE(i.qty,1) AS qty, i.unit, i.raw_name "
        "FROM items i JOIN receipts r ON r.id=i.receipt_id "
        "WHERE i.product_id > 0 AND r.status='ok' AND r.purchased_on IS NOT NULL ORDER BY r.purchased_on"
    ):
        out.setdefault(r["pid"], []).append(r)
    return out


# typisk pakning når størrelsen ikke står på kvitteringen
DEFAULT_PACK = {"pasta": 0.5, "havregryn": 1.0, "uht": 1.0}


def _amount(row, unit, key=None):
    """Hvor mye et kjøp tilsvarer i beredskapsvarens enhet (kg/liter regnes fra pakningsstørrelsen)."""
    import parse
    qty = row["qty"] if row["qty"] and row["qty"] > 0 else 1
    if unit in ("kg", "liter"):
        if (row["unit"] or "").lower() == "kg" and unit == "kg":
            return qty
        amt, u = parse.package_size(row["raw_name"])
        if amt and ((u == "kg" and unit == "kg") or (u == "l" and unit == "liter")):
            return qty * amt
        return qty * DEFAULT_PACK[key] if key in DEFAULT_PACK else None
    if unit == "stk":
        amt, u = parse.package_size(row["raw_name"])
        if amt and u == "stk":
            return qty * amt  # f.eks. bleier 44 stk
    return qty



def _state():
    s = db.get_setting("beredskap") or {}
    hh = {**DEFAULT_HOUSEHOLD, **(s.get("household") or {})}
    if "household" not in s:
        # første gang: bruker familien bleier? (kjøpt bleier jevnlig siste 3 mnd)
        for p in logic.product_stats():
            if re.search(r"bleie", p["name"], re.I) and p.get("q90", 0) >= 2:
                hh["diaper_kids"] = 1
                break
    return {"household": hh, "items": s.get("items") or {}, "custom": s.get("custom") or []}


def _save(st):
    db.set_setting("beredskap", st)


def need_for(item, hh):
    if item.get("kind") == "check":
        return 1
    a, c = hh["adults"], hh["children"]
    days = hh.get("days") or 7
    if "d" in item:  # bleier: per barn med bleie
        n = item["d"] * hh.get("diaper_kids", 0)
    else:
        n = item.get("a", 0) * a + item.get("c", 0) * c + item.get("f", 0)
    if item.get("scale"):
        n = n * days / 7
    if item["unit"] in ("kg", "liter") and n < 10:
        return round(math.ceil(n * 2) / 2, 1)  # halve kilo/liter
    return int(math.ceil(n - 1e-9))


def compute():
    st = _state()
    hh = st["household"]
    today = logic.today()
    soon = today + timedelta(days=60)
    out = []
    stats = logic.product_stats()
    by_id = {p["id"]: p for p in stats}
    buys = _purchases()
    defs = ITEMS + [{**c, "src": "egen", "custom": True, "kind": c.get("kind") or "qty"} for c in st["custom"]]
    for it in defs:
        rec = st["items"].get(it["key"], {})
        need_default = int(it.get("need") or 1) if it.get("custom") else need_for(it, hh)
        # egen mengde overstyrer anbefalingen (gjelder til den nullstilles)
        need = rec["need"] if rec.get("need") is not None and it.get("kind") != "check" else need_default
        na = bool(rec.get("na")) or (it["key"] == "bleier" and not hh.get("diaper_kids"))
        have = rec.get("have") or 0
        if it.get("kind") == "check":
            have = 1 if have else 0
        # kobling til lageret: valgt manuelt (links) eller funnet automatisk ut fra varenavnet
        if rec.get("links") is not None:
            linked = [by_id[i] for i in rec["links"] if i in by_id]
        elif it["key"] in _LINK_RE:
            linked = [p for p in stats if _LINK_RE[it["key"]].search(p["name"]) and not p.get("manual")]
        else:
            linked = []
        last_buy = max((p["last"] for p in linked), default=None)
        bought_since = 0
        counted = rec.get("counted_at")
        if linked and counted and it.get("kind") != "check":
            for p in linked:
                for b in buys.get(p["id"], []):
                    if b["d"] > counted:
                        a = _amount(b, it.get("unit"), it["key"])
                        if a:
                            bought_since += a
        bought_since = round(bought_since, 1)
        exp = rec.get("expiry") or None
        exp_est = False
        keep = it.get("keep")
        if not exp and linked and last_buy and keep and have:
            # rullerende: beredskapen er fornyet ved siste kjøp (forutsetter at eldste brukes først)
            d = date.fromisoformat(last_buy)
            m = d.month - 1 + keep
            exp = date(d.year + m // 12, m % 12 + 1, min(d.day, 28)).isoformat()
            exp_est = True
        stale = bool(linked and last_buy and keep and have and not rec.get("expiry")
                     and (today - date.fromisoformat(last_buy)).days > (keep - 2) * 30)
        status = "na" if na else ("ok" if have >= need else ("part" if have > 0 else "missing"))
        expiring = bool(exp and not na and have and exp <= soon.isoformat()) or (stale and not na)
        expired = bool(exp and not na and have and exp < today.isoformat())
        out.append({
            "key": it["key"], "cat": it.get("cat") or "Nyttig å ha", "name": it["name"], "unit": it.get("unit", ""),
            "kind": it.get("kind", "qty"), "src": it.get("src", "tips"), "note": it.get("note", ""),
            "need": need, "need_default": need_default, "need_custom": need != need_default, "have": have, "na": na, "expiry": exp, "status": status,
            "expiring": expiring, "expired": expired, "kcal": it.get("kcal"), "custom": bool(it.get("custom")),
            "auto_na": it["key"] == "bleier" and not hh.get("diaper_kids"),
            "linked": [{"id": p["id"], "name": p["name"], "since": p["since"], "rhythm": p.get("rhythm"),
                        "in_stock": p["in_stock"]} for p in linked],
            "links_manual": rec.get("links") is not None,
            "last_buy": last_buy, "bought_since": bought_since if not na else 0,
            "expiry_est": exp_est, "stale": stale and not na,
        })
    active = [i for i in out if not i["na"]]
    score = sum(min(1, i["have"] / i["need"]) if i["need"] else 1 for i in active)
    days = hh.get("days") or 7
    people = hh["adults"] + hh["children"]
    water = next(i for i in out if i["key"] == "vann")
    water_days = round(water["have"] / (20 * people / 7), 1) if people else 0
    kcal_day = hh["adults"] * KCAL_ADULT + hh["children"] * KCAL_CHILD
    kcal_have = sum((i["have"] or 0) * (i["kcal"] or 0) for i in out if i["kcal"] and not i["na"])
    food_days = round(kcal_have / kcal_day, 1) if kcal_day else 0
    cats = []
    for c in CATS + sorted({i["cat"] for i in out} - set(CATS)):
        items = [i for i in out if i["cat"] == c]
        if not items:
            continue
        act = [i for i in items if not i["na"]]
        cats.append({"cat": c, "items": items, "done": sum(1 for i in act if i["status"] == "ok"), "total": len(act)})
    return {
        "household": hh,
        "people": people,
        "days": days,
        "percent": round(100 * score / len(active)) if active else 0,
        "done": sum(1 for i in active if i["status"] == "ok"),
        "total": len(active),
        "water": {"have": water["have"], "need": water["need"], "days": water_days},
        "food": {"kcal_have": round(kcal_have), "kcal_need": kcal_day * days, "kcal_day": kcal_day, "days": food_days},
        "expiring": [i for i in out if i["expiring"]],
        "missing": [i for i in active if i["status"] != "ok"],
        "categories": cats,
        "candidates": [{"id": p["id"], "name": p["name"], "category": p["category"]} for p in sorted(stats, key=lambda p: p["name"].lower()) if not p.get("manual")],
    }


def set_household(data):
    st = _state()
    hh = st["household"]
    for k in DEFAULT_HOUSEHOLD:
        if k in data:
            hh[k] = max(0, min(30, int(data[k])))
    hh["days"] = max(1, hh["days"])
    st["household"] = hh
    _save(st)


def set_item(key, data):
    st = _state()
    if key not in BY_KEY and not any(c["key"] == key for c in st["custom"]):
        raise KeyError(key)
    rec = st["items"].get(key, {})
    if "have" in data:
        rec["have"] = max(0, float(data["have"] or 0))
        if rec["have"] == int(rec["have"]):
            rec["have"] = int(rec["have"])
        rec["counted_at"] = logic.today().isoformat()  # kjøp etter dette foreslås lagt til
    if "need" in data:
        if data["need"] is None or data["need"] == "":
            rec.pop("need", None)
        else:
            v = max(0, float(data["need"]))
            rec["need"] = int(v) if v == int(v) else round(v, 1)
    if "links" in data:
        rec["links"] = None if data["links"] is None else [int(x) for x in data["links"]]
    if "expiry" in data:
        rec["expiry"] = data["expiry"] or None
    if "na" in data:
        rec["na"] = bool(data["na"])
    st["items"][key] = rec
    _save(st)


def add_custom(name, cat, need, unit, kind="qty"):
    st = _state()
    key = "egen_" + re.sub(r"[^a-z0-9æøå]+", "_", name.strip().lower())[:30]
    i = 2
    base = key
    while key in BY_KEY or any(c["key"] == key for c in st["custom"]):
        key = f"{base}_{i}"
        i += 1
    st["custom"].append({"key": key, "name": name.strip(), "cat": cat or "Nyttig å ha",
                         "need": max(1, int(need or 1)), "unit": unit or "stk", "kind": kind})
    _save(st)
    return key


def remove_custom(key):
    st = _state()
    st["custom"] = [c for c in st["custom"] if c["key"] != key]
    st["items"].pop(key, None)
    _save(st)


def shopping_text(i):
    """Tekst til handlelista for det som mangler, f.eks. «Hermetisk fisk – 6 boks (beredskap)»."""
    if i["kind"] == "check":
        return f"{i['name']} (beredskap)"
    miss = i["need"] - (i["have"] or 0)
    miss = round(miss, 1) if isinstance(miss, float) and miss != int(miss) else int(miss)
    unit = f" {i['unit']}" if i["unit"] else ""
    return f"{i['name']} – {str(miss).replace('.', ',')}{unit} (beredskap)"


def product_links():
    """For lageret og handlelista: hvilke produkter som er del av beredskapen.
    {product_id: {"key", "name", "need", "have", "unit", "status"}}"""
    try:
        d = compute()
    except Exception:
        return {}
    out = {}
    for c in d["categories"]:
        for i in c["items"]:
            if i["na"]:
                continue
            for p in i["linked"]:
                out[p["id"]] = {"key": i["key"], "name": i["name"], "need": i["need"], "have": i["have"],
                                "unit": i["unit"], "status": i["status"]}
    return out
