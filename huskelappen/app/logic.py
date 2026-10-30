"""Forslag, godkjenning, kalender, veggskjerm og varsler."""
import json
import logging
import urllib.request
from datetime import date, datetime, timedelta

import db
import ha
import util
from config import HA_TOKEN, SUPERVISOR_URL

log = logging.getLogger("huskelappen")

SENSOR = "sensor.huskelappen_barnehagen"
SHOW = "binary_sensor.huskelappen_vis"
ICONS = {"ha_med": "🎒", "spesiell_dag": "🌲", "stengt": "🚫", "foreldre": "👪", "frist": "⏰", "info": "ℹ️"}


# --- Lesing -------------------------------------------------------------------

def _row(r):
    r = dict(r)
    r["bring"] = json.loads(r.get("bring") or "[]")
    r["children"] = json.loads(r.get("children") or "[]")
    return r


def items(status=None, from_day=None):
    sql, args = "SELECT * FROM items WHERE 1=1", []
    if status:
        sql += " AND status=?"
        args.append(status)
    if from_day:
        sql += " AND COALESCE(end_day, day) >= ?"
        args.append(from_day)
    sql += " ORDER BY day, COALESCE(start_time, ''), id"
    return [_row(r) for r in db.q(sql, args)]


def items_on(d, status="approved"):
    ds = d.isoformat()
    rows = db.q(
        "SELECT * FROM items WHERE status=? AND day<=? AND COALESCE(end_day, day)>=? "
        "ORDER BY COALESCE(start_time, ''), id",
        (status, ds, ds),
    )
    # flerdagers «info» (f.eks. en ny fast ordning) vises bare første dag
    return [_row(r) for r in rows if r["kind"] != "info" or r["day"] == ds]


def pending_count():
    return db.one("SELECT COUNT(*) AS n FROM items WHERE status='pending' AND COALESCE(end_day, day)>=?",
                  (util.today().isoformat(),))["n"]


# --- Endring og godkjenning ------------------------------------------------------

EDITABLE = ("day", "end_day", "start_time", "end_time", "title", "bring", "details", "children", "kind")


def update(iid, data):
    it = db.one("SELECT * FROM items WHERE id=?", (iid,))
    if not it:
        raise KeyError("Fant ikke punktet")
    if it["status"] == "approved":
        raise ValueError("Punktet er allerede lagt i kalenderen")
    for k in EDITABLE:
        if k in data:
            v = data[k]
            if k in ("bring", "children"):
                v = json.dumps([x for x in (v or []) if str(x).strip()], ensure_ascii=False)
            elif v == "":
                v = None
            db.ex(f"UPDATE items SET {k}=? WHERE id=?", (v, iid))
    return _row(db.one("SELECT * FROM items WHERE id=?", (iid,)))


def add_manual(data):
    d = date.fromisoformat(data["day"])
    iid = db.ex(
        "INSERT INTO items(msgid, day, title, bring, details, children, kind, dedupe_key, source_subject) "
        "VALUES('manuell',?,?,?,?,?,?,?, 'Lagt til for hånd')",
        (d.isoformat(), data.get("title") or "Husk", json.dumps(data.get("bring") or [], ensure_ascii=False),
         data.get("details") or "", json.dumps(data.get("children") or [], ensure_ascii=False),
         data.get("kind") or "ha_med", f"manuell|{datetime.now().timestamp()}"),
    )
    return update(iid, {k: v for k, v in data.items() if k in ("start_time", "end_time", "end_day")})


def reject(iid):
    db.ex("UPDATE items SET status='rejected', decided_at=datetime('now') WHERE id=? AND status='pending'", (iid,))


def restore(iid):
    db.ex("UPDATE items SET status='pending', decided_at=NULL WHERE id=? AND status='rejected'", (iid,))


def calendar_for(name, s=None):
    s = s or db.all_settings()
    c = next((c for c in s.get("children") or [] if c["name"] == name), None)
    return (c or {}).get("calendar") or None


def event_payload(it, entity):
    desc = []
    if it["bring"]:
        desc.append("Ha med: " + ", ".join(it["bring"]))
    if it.get("details"):
        desc.append(it["details"])
    if it.get("msgid") == "manuell":
        desc.append("Lagt til i Huskelappen")
    else:
        desc.append(f"Fra barnehagen: {it.get('source_subject') or ''} (Huskelappen)")
    data = {"entity_id": entity, "summary": f"{ICONS.get(it['kind'], '')} {it['title']}".strip(),
            "description": "\n".join(desc)}
    d = date.fromisoformat(it["day"])
    if it.get("start_time"):
        sh, sm = util.hm(it["start_time"], "08:00")
        start = datetime(d.year, d.month, d.day, sh, sm)
        if it.get("end_time"):
            eh, em = util.hm(it["end_time"], "09:00")
            end = datetime(d.year, d.month, d.day, eh, em)
        else:
            end = start + timedelta(minutes=45)
        if end <= start:
            end = start + timedelta(minutes=45)
        data["start_date_time"] = start.strftime("%Y-%m-%d %H:%M:%S")
        data["end_date_time"] = end.strftime("%Y-%m-%d %H:%M:%S")
    else:
        last = date.fromisoformat(it["end_day"]) if it.get("end_day") else d
        data["start_date"] = d.isoformat()
        data["end_date"] = (last + timedelta(days=1)).isoformat()
    return data


def approve(iid):
    it = db.one("SELECT * FROM items WHERE id=?", (iid,))
    if not it:
        raise KeyError("Fant ikke punktet")
    if it["status"] == "approved":
        return _row(it)
    it = _row(it)
    s = db.all_settings()
    done, missing, errors = [], [], []
    for name in it["children"]:
        entity = calendar_for(name, s)
        if not entity:
            missing.append(name)
            continue
        try:
            ha.call_service("calendar", "create_event", event_payload(it, entity))
            done.append(name)
        except ha.HAError as e:
            errors.append(f"{name}: {e}")
    if errors or (missing and not done):
        raise RuntimeError("; ".join(errors) or f"Mangler kalender for {', '.join(missing)} (se Innstillinger)")
    note = f"Lagt i kalenderen til {', '.join(done)}"
    if missing:
        note += f" (mangler kalender for {', '.join(missing)})"
    db.ex("UPDATE items SET status='approved', decided_at=datetime('now'), calendar_note=? WHERE id=?",
          (note, iid))
    push_sensors()
    return _row(db.one("SELECT * FROM items WHERE id=?", (iid,)))


# --- Tekst til skjerm og varsel ---------------------------------------------------

def _who(kids, all_names):
    if set(kids) >= set(all_names) and len(all_names) > 1:
        return "begge" if len(all_names) == 2 else "alle"
    return " og ".join(kids)


def day_lines(d, markdown=True):
    s = db.all_settings()
    names = [c["name"] for c in s.get("children") or []]
    lines = []
    for it in items_on(d):
        when = f" kl. {it['start_time']}" + (f"–{it['end_time']}" if it.get("end_time") else "") \
            if it.get("start_time") else ""
        who = _who(it["children"], names)
        head = f"{ICONS.get(it['kind'], '•')} " + (f"**{it['title']}**" if markdown else it["title"])
        lines.append(f"{head}{when} ({who})")
        if it["bring"]:
            lines.append(("  Ha med: " if markdown else "   Ha med: ") + ", ".join(it["bring"]))
    return lines


def screen_window(now=None):
    """Hvilken dag skjermen skal vise nå, eller None (mellom hide_time og evening_time)."""
    now = now or util.now()
    s = db.all_settings()
    eh, em = util.hm(s.get("evening_time"), "19:00")
    hh, hm_ = util.hm(s.get("hide_time"), "09:00")
    t = (now.hour, now.minute)
    if t >= (eh, em):
        return now.date() + timedelta(days=1), "I morgen"
    if t < (hh, hm_):
        return now.date(), "I dag"
    return None, None


def push_sensors(now=None):
    try:
        d, label = screen_window(now)
        lines = day_lines(d) if d else []
        show = bool(lines)
        if show:
            text = f"### {label} i barnehagen – {util.nice_date(d)}\n\n" + "\n\n".join(lines)
            state = (f"{label}: " + "; ".join(it["title"] for it in items_on(d)))[:250]
        else:
            text, state = "", "Ingenting spesielt"
        ha._req("POST", f"/states/{SENSOR}", {"state": state, "attributes": {
            "friendly_name": "Huskelappen – barnehagen",
            "icon": "mdi:bag-personal",
            "tekst": text,
            "dato": d.isoformat() if d else None,
            "venter_godkjenning": pending_count(),
        }})
        ha._req("POST", f"/states/{SHOW}", {"state": "on" if show else "off", "attributes": {
            "friendly_name": "Huskelappen – vis på skjermen",
            "icon": "mdi:monitor-eye",
        }})
    except Exception as e:  # HA kan være nede – prøv igjen neste runde
        log.warning("Kunne ikke oppdatere sensorene: %s", e)


_ingress = None


def ingress_path():
    global _ingress
    if _ingress is None:
        try:
            req = urllib.request.Request(f"{SUPERVISOR_URL}/addons/self/info",
                                         headers={"Authorization": f"Bearer {HA_TOKEN}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                slug = json.loads(r.read()).get("data", {}).get("slug")
            _ingress = f"/hassio/ingress/{slug}" if slug else ""
        except Exception:
            return None
    return _ingress or None


def notify(title, message, open_app=True):
    svc = db.get_setting("notify_service")
    if not svc or "." not in svc:
        log.info("Ingen varslingstjeneste valgt – hopper over varsel: %s", title)
        return False
    domain, name = svc.split(".", 1)
    data = {"title": title, "message": message}
    path = ingress_path() if open_app else None
    if path:
        data["data"] = {"url": path, "clickAction": path, "tag": "huskelappen"}
    ha.call_service(domain, name, data)
    return True


def next_review(now=None):
    now = now or util.now()
    s = db.all_settings()
    wd = int(s.get("review_weekday", 6))
    h, m = util.hm(s.get("review_time"), "18:00")
    cand = now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=(wd - now.weekday()) % 7)
    if cand <= now:
        cand += timedelta(days=7)
    return cand


def after_new_items(new):
    """Varsler med en gang hvis noe nytt gjelder før neste søndagsgjennomgang."""
    if not new or not db.get_setting("urgent_notify"):
        return
    limit = next_review().date()
    soon = [it for it in new if date.fromisoformat(it["day"]) <= limit]
    if not soon:
        return
    lines = [f"{util.nice_date(date.fromisoformat(it['day'])).capitalize()}: {it['title']}" for it in soon[:5]]
    notify("Ny beskjed fra barnehagen",
           "Gjelder før neste gjennomgang – godkjenn i Huskelappen:\n" + "\n".join(lines))


def tick(now=None):
    """Kjøres hvert minutt: kveldsvarsel og søndagspåminnelse (hver bare én gang)."""
    now = now or util.now()
    s = db.all_settings()
    t = (now.hour, now.minute)

    eh, em = util.hm(s.get("evening_time"), "19:00")
    if s.get("evening_notify") and (eh, em) <= t < (eh + 2, em):
        if db.mark_sent(f"evening:{now.date().isoformat()}"):
            tomorrow = now.date() + timedelta(days=1)
            lines = day_lines(tomorrow, markdown=False)
            waiting = [it for it in items("pending") if it["day"] == tomorrow.isoformat()]
            if waiting:
                lines.append(f"({len(waiting)} forslag for i morgen venter på godkjenning)")
            if lines:
                notify(f"Barnehagen i morgen – {util.nice_date(tomorrow)}", "\n".join(lines))

    rh, rm = util.hm(s.get("review_time"), "18:00")
    if now.weekday() == int(s.get("review_weekday", 6)) and (rh, rm) <= t < (rh + 4, rm):
        if db.mark_sent(f"review:{now.date().isoformat()}"):
            n = pending_count()
            if n:
                notify("Huskelappen: gå gjennom uka",
                       f"{n} {'forslag' if n != 1 else 'forslag'} fra barnehagen venter på godkjenning før uka starter.")
