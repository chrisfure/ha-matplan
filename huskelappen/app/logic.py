"""Forslag, godkjenning, kalender, faste påminnelser, veggskjerm, varsler og handleliste."""
import json
import logging
import urllib.request
from datetime import date, datetime, timedelta

import db
import ha
import util
import weather
from config import HA_TOKEN, SUPERVISOR_URL

log = logging.getLogger("huskelappen")

SENSOR = "sensor.huskelappen_barnehagen"
SHOW = "binary_sensor.huskelappen_vis"
ICONS = {"ha_med": "🎒", "spesiell_dag": "🌲", "stengt": "🚫", "foreldre": "👪", "frist": "⏰",
         "info": "ℹ️", "fast": "🔁"}
ACTION_APPROVE_ALL = "HUSKELAPPEN_GODKJENN_ALLE"
LUNCH_WORDS = ("matpakke", "matpakker", "niste", "lunsj med", "turdag", "utflukt", " tur ")


# --- Lesing -------------------------------------------------------------------

def _row(r):
    r = dict(r)
    for k in ("bring", "children", "weekdays"):
        r[k] = json.loads(r.get(k) or "[]")
    return r


def kid_names(s=None):
    s = s or db.all_settings()
    return [c["name"] for c in s.get("children") or [] if c.get("name")]


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
        "SELECT * FROM items WHERE status=? AND kind!='fast' AND day<=? AND COALESCE(end_day, day)>=? "
        "ORDER BY COALESCE(start_time, ''), id",
        (status, ds, ds),
    )
    # flerdagers «info» vises bare første dag
    return [_row(r) for r in rows if r["kind"] != "info" or r["day"] == ds]


def pending_count():
    return db.one("SELECT COUNT(*) AS n FROM items WHERE status='pending' AND COALESCE(end_day, day)>=?",
                  (util.today().isoformat(),))["n"]


# --- Faste påminnelser ----------------------------------------------------------

def fixed_list(active_only=False):
    rows = db.q("SELECT * FROM fixed" + (" WHERE active=1" if active_only else "") + " ORDER BY id")
    return [_row(r) for r in rows]


def fixed_on(d):
    return [f for f in fixed_list(True) if d.weekday() in f["weekdays"]]


def add_fixed(data):
    wds = sorted({int(w) for w in data.get("weekdays") or [] if 0 <= int(w) <= 6})
    if not wds:
        raise ValueError("Velg minst én ukedag")
    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("Skriv hva som skal huskes")
    fid = db.ex("INSERT INTO fixed(weekdays, title, bring, children) VALUES(?,?,?,?)",
                (json.dumps(wds), title[:60],
                 json.dumps([b for b in data.get("bring") or [] if str(b).strip()], ensure_ascii=False),
                 json.dumps(data.get("children") or kid_names(), ensure_ascii=False)))
    push_sensors()
    return fid


def delete_fixed(fid):
    db.ex("DELETE FROM fixed WHERE id=?", (fid,))
    push_sensors()


def set_fixed_active(fid, on):
    db.ex("UPDATE fixed SET active=? WHERE id=?", (1 if on else 0, fid))
    push_sensors()


def fixed_covers(title, weekdays):
    """Finnes det allerede en fast påminnelse som dekker dette?"""
    for f in fixed_list(True):
        if util.similar(f["title"], title) and set(f["weekdays"]) & set(weekdays):
            return True
    return False


# --- Endring og godkjenning ------------------------------------------------------

EDITABLE = ("day", "end_day", "start_time", "end_time", "title", "bring", "details", "children", "kind",
            "weekdays")


def update(iid, data):
    it = db.one("SELECT * FROM items WHERE id=?", (iid,))
    if not it:
        raise KeyError("Fant ikke punktet")
    if it["status"] == "approved":
        raise ValueError("Punktet er allerede godkjent")
    for k in EDITABLE:
        if k in data:
            v = data[k]
            if k in ("bring", "children", "weekdays"):
                v = json.dumps([x for x in (v or []) if str(x).strip()], ensure_ascii=False)
            elif v == "":
                v = None
            db.ex(f"UPDATE items SET {k}=? WHERE id=?", (v, iid))
    return _row(db.one("SELECT * FROM items WHERE id=?", (iid,)))


def add_manual(data):
    d = date.fromisoformat(data["day"])
    iid = db.ex(
        "INSERT INTO items(msgid, day, title, bring, details, children, kind, dedupe_key, source_subject, source) "
        "VALUES('manuell',?,?,?,?,?,?,?, 'Lagt til for hånd', 'Lagt til for hånd')",
        (d.isoformat(), data.get("title") or "Husk", json.dumps(data.get("bring") or [], ensure_ascii=False),
         data.get("details") or "", json.dumps(data.get("children") or [], ensure_ascii=False),
         data.get("kind") or "ha_med", f"manuell|{datetime.now().timestamp()}"),
    )
    return update(iid, {k: v for k, v in data.items() if k in ("start_time", "end_time", "end_day")})


def reject(iid, by="user", note=None):
    db.ex("UPDATE items SET status='rejected', decided_at=datetime('now'), decided_by=?, auto_note=? "
          "WHERE id=? AND status='pending'", (by, note, iid))


def restore(iid):
    db.ex("UPDATE items SET status='pending', decided_at=NULL, decided_by=NULL, auto_note=NULL "
          "WHERE id=? AND status='rejected'", (iid,))


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
        desc.append(f"Fra {it.get('source') or 'barnehagen'}: {it.get('source_subject') or ''} (Huskelappen)")
    data = {"entity_id": entity, "summary": f"{ICONS.get(it['kind'], '')} {it['title']}".strip(),
            "description": "\n".join(desc)}
    d = date.fromisoformat(it["day"])
    if it.get("start_time"):
        sh, sm = util.hm(it["start_time"], "08:00")
        start = datetime(d.year, d.month, d.day, sh, sm)
        end = start + timedelta(minutes=45)
        if it.get("end_time"):
            eh, em = util.hm(it["end_time"], "09:00")
            end = max(datetime(d.year, d.month, d.day, eh, em), start + timedelta(minutes=5))
        data["start_date_time"] = start.strftime("%Y-%m-%d %H:%M:%S")
        data["end_date_time"] = end.strftime("%Y-%m-%d %H:%M:%S")
    else:
        last = date.fromisoformat(it["end_day"]) if it.get("end_day") else d
        data["start_date"] = d.isoformat()
        data["end_date"] = (last + timedelta(days=1)).isoformat()
    return data


def approve(iid, by="user", note=None):
    it = db.one("SELECT * FROM items WHERE id=?", (iid,))
    if not it:
        raise KeyError("Fant ikke punktet")
    if it["status"] == "approved":
        return _row(it)
    it = _row(it)
    if it["kind"] == "fast":
        wds = it["weekdays"] or [date.fromisoformat(it["day"]).weekday()]
        if not fixed_covers(it["title"], wds):
            add_fixed({"weekdays": wds, "title": it["title"], "bring": it["bring"], "children": it["children"]})
        cal_note = f"Fast påminnelse {util.weekdays_text(wds)}"
    else:
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
        cal_note = f"Lagt i kalenderen til {', '.join(done)}"
        if missing:
            cal_note += f" (mangler kalender for {', '.join(missing)})"
    db.ex("UPDATE items SET status='approved', decided_at=datetime('now'), calendar_note=?, decided_by=?, "
          "auto_note=? WHERE id=?", (cal_note, by, note, iid))
    try:
        shopping_check()
    except Exception as e:
        log.warning("Handleliste: %s", e)
    push_sensors()
    return _row(db.one("SELECT * FROM items WHERE id=?", (iid,)))


def approve_all(by="user"):
    ok, errors = 0, []
    for it in items("pending", util.today().isoformat()):
        try:
            approve(it["id"], by=by)
            ok += 1
        except Exception as e:
            errors.append(f"{it['title']}: {e}")
    return ok, errors


# --- Læring ---------------------------------------------------------------------

def learn(it):
    """Avviser eller godkjenner et nytt forslag ut fra hva du har gjort med lignende før.
    Returnerer ny status («pending», «rejected» eller «approved»)."""
    s = db.all_settings()
    past = db.q("SELECT title, status FROM items WHERE kind=? AND decided_by='user' AND id!=?",
                (it["kind"], it["id"]))
    sim = [p for p in past if util.similar(p["title"], it["title"])]
    rej = sum(1 for p in sim if p["status"] == "rejected")
    app = sum(1 for p in sim if p["status"] == "approved")
    if s.get("learn_reject") and rej >= 2 and rej > app:
        reject(it["id"], by="auto", note=f"Avvist automatisk – du har avvist lignende {rej} ganger")
        return "rejected"
    if s.get("learn_approve") and app >= 3 and rej == 0:
        try:
            approve(it["id"], by="auto", note=f"Godkjent automatisk – du har godkjent lignende {app} ganger")
            return "approved"
        except Exception as e:
            log.warning("Automatisk godkjenning feilet: %s", e)
    return "pending"


# --- Tekst til skjerm og varsel ---------------------------------------------------

def _who(kids, all_names):
    if set(kids) >= set(all_names) and len(all_names) > 1:
        return "begge" if len(all_names) == 2 else "alle"
    return " og ".join(kids)


def day_lines(d, markdown=True, with_weather=True):
    s = db.all_settings()
    names = kid_names(s)
    b = (lambda x: f"**{x}**") if markdown else (lambda x: x)
    pad = "  " if markdown else "   "
    lines = []
    todays = items_on(d)
    for it in todays:
        when = ""
        if it.get("start_time"):
            when = f" kl. {it['start_time']}" + (f"–{it['end_time']}" if it.get("end_time") else "")
        title = ("Frist: " if it["kind"] == "frist" else "") + it["title"]
        lines.append(f"{ICONS.get(it['kind'], '•')} {b(title)}{when} ({_who(it['children'], names)})")
        if it["bring"]:
            lines.append(f"{pad}Ha med: " + ", ".join(it["bring"]))
    for f in fixed_on(d):
        lines.append(f"🔁 {b(f['title'])} ({_who(f['children'], names)})")
        if f["bring"]:
            lines.append(f"{pad}Ha med: " + ", ".join(f["bring"]))
    closed = any(it["kind"] == "stengt" for it in todays)
    if with_weather and not closed and (lines or (s.get("weather_daily") and d.weekday() < 5)):
        w = weather.day_advice(d)
        if w:
            lines.append(f"🌦 {b('Været')}: {w[0]}")
            lines.append(f"{pad}Klær: {w[1]}")
    return lines


def week_lines(start, days, markdown=True):
    """Kort oversikt per dag: «**Tirsdag**: Gymtøy, Fotografering»."""
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        titles = [it["title"] for it in items_on(d)] + [f["title"] for f in fixed_on(d)]
        if titles:
            name = util.WEEKDAYS[d.weekday()].capitalize()
            out.append((f"**{name}**" if markdown else name) + ": " + ", ".join(titles))
    return out


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


def screen_content(now=None, preview=False):
    """(dag, etikett, markdown-tekst, linjer). Med preview=True vises morgendagen også utenom visningstiden."""
    now = now or util.now()
    d, label = screen_window(now)
    if not d:
        if not preview:
            return None, None, "", []
        d, label = now.date() + timedelta(days=1), "I morgen"
    s = db.all_settings()
    lines = day_lines(d)
    parts = list(lines)
    if (s.get("weekly_summary") and label == "I morgen"
            and now.weekday() == int(s.get("review_weekday", 6))):
        rest = week_lines(d + timedelta(days=1), 4)
        if rest:
            parts = (parts or ["Ingenting spesielt i morgen."]) + ["**Resten av uka**"] + rest
    if not parts:
        return d, label, "", []
    text = f"### {label} – {util.nice_date(d)}\n\n" + "\n\n".join(parts)
    return d, label, text, parts


def push_sensors(now=None):
    try:
        d, label, text, parts = screen_content(now)
        show = bool(text)
        if show:
            titles = [it["title"] for it in items_on(d)] + [f["title"] for f in fixed_on(d)]
            state = (f"{label}: " + ("; ".join(titles) if titles else "vær og klær"))[:250]
        else:
            state = "Ingenting spesielt"
        ha._req("POST", f"/states/{SENSOR}", {"state": state, "attributes": {
            "friendly_name": "Huskelappen",
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


# --- Varsler ----------------------------------------------------------------------

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


def recipients(audience="all", s=None):
    """audience «primary» = bare hovedmottaker (godkjenning), «all» = også den andre hvis avkrysset."""
    s = s or db.all_settings()
    out = []
    if s.get("notify_service"):
        out.append(("primary", s["notify_service"]))
    extra = s.get("notify_extra")
    if audience == "all" and s.get("notify_extra_on") and extra and extra != s.get("notify_service"):
        out.append(("extra", extra))
    return out


def notify(title, message, audience="all", approve_button=False, primary_suffix=""):
    """Sender varsel. primary_suffix legges bare til hos hovedmottakeren (f.eks. «3 forslag venter»)."""
    sent = 0
    path = ingress_path()
    for who, svc in recipients(audience):
        if "." not in svc:
            continue
        domain, name = svc.split(".", 1)
        msg = message + (("\n" + primary_suffix) if (who == "primary" and primary_suffix) else "")
        if not msg.strip():
            continue
        data = {"title": title, "message": msg.strip()}
        extra = {"tag": "huskelappen-" + util.norm(title).replace(" ", "-")[:40]}
        if path:
            extra.update({"url": path, "clickAction": path})
        actions = []
        if approve_button and who == "primary":
            actions.append({"action": ACTION_APPROVE_ALL, "title": "Godkjenn alle"})
        if path and actions:
            actions.append({"action": "URI", "title": "Åpne", "uri": path})
        if actions:
            extra["actions"] = actions
        data["data"] = extra
        try:
            ha.call_service(domain, name, data)
            sent += 1
        except ha.HAError as e:
            log.warning("Varsel til %s feilet: %s", svc, e)
    if not sent:
        log.info("Ingen varsel sendt (ingen mottaker valgt?): %s", title)
    return sent


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
    """Varsler hovedmottakeren med en gang hvis noe nytt gjelder før neste gjennomgang."""
    if not new or not db.get_setting("urgent_notify"):
        return
    limit = next_review().date()
    soon = [it for it in new if date.fromisoformat(it["day"]) <= limit]
    if not soon:
        return
    lines = [f"{util.nice_date(date.fromisoformat(it['day'])).capitalize()}: {it['title']}" for it in soon[:5]]
    notify("Ny beskjed som haster", "Gjelder før neste gjennomgang:\n" + "\n".join(lines),
           audience="primary", approve_button=True)


def handle_action(action):
    """Knapp trykket i et varsel."""
    if action != ACTION_APPROVE_ALL:
        return None
    ok, errors = approve_all(by="user")
    msg = f"{ok} lagt i kalenderen." if ok else "Det var ingenting som ventet."
    if errors:
        msg += " Feil: " + "; ".join(errors)[:300]
    notify("Huskelappen", msg, audience="primary")
    return msg


def _evening(now, s):
    tomorrow = now.date() + timedelta(days=1)
    lines = day_lines(tomorrow, markdown=False)
    waiting = [it for it in items("pending") if it["day"] == tomorrow.isoformat()]
    suffix = f"({len(waiting)} forslag for i morgen venter på godkjenning)" if waiting else ""
    if lines:
        notify(f"I morgen – {util.nice_date(tomorrow)}", "\n".join(lines), audience="all",
               primary_suffix=suffix, approve_button=bool(waiting))
    elif suffix:
        notify(f"I morgen – {util.nice_date(tomorrow)}", "", audience="all", primary_suffix=suffix,
               approve_button=True)


def _deadlines(now, s):
    days = int(s.get("deadline_days") or 2)
    if days < 2:
        return  # dagen før dekkes av kveldsvarselet
    target = now.date() + timedelta(days=days)
    for it in items_on(target):
        if it["kind"] == "frist" and db.mark_sent(f"frist:{it['id']}:{days}"):
            notify(f"Frist {util.nice_date(target)}", f"⏰ {it['title']}" +
                   (f"\n{it['details']}" if it.get("details") else ""), audience="all")


def _review(now, s):
    n = pending_count()
    summary = []
    if s.get("weekly_summary"):
        summary = week_lines(now.date() + timedelta(days=1), 7, markdown=False)
    suffix = f"{n} forslag venter på godkjenning før uka starter." if n else ""
    if summary or suffix:
        notify("Uka som kommer", "\n".join(summary), audience="all" if summary else "primary",
               primary_suffix=suffix, approve_button=bool(n))


def shopping_check(now=None):
    """Legger matpakkemat på handlelista (den samme som Matplan bruker) noen dager før tur/matpakkedager."""
    s = db.all_settings()
    ent = s.get("todo_entity")
    if not ent:
        return 0
    now = now or util.now()
    lead = int(s.get("lunch_days_before") or 2)
    days = []
    for i in range(1, lead + 1):
        d = now.date() + timedelta(days=i)
        hits = [it for it in items_on(d) if _needs_lunch(it)] + [f for f in fixed_on(d) if _needs_lunch(f)]
        if hits and db.one("SELECT 1 FROM sent WHERE key=?", (f"todo:{d.isoformat()}",)) is None:
            days.append(d)
    if not days:
        return 0
    names = ", ".join(util.WEEKDAYS[d.weekday()] for d in days)
    ha.call_service("todo", "add_item", {"entity_id": ent, "item": f"Brød og pålegg til matpakke ({names})",
                                         "description": "Lagt til av Huskelappen"})
    for d in days:
        db.mark_sent(f"todo:{d.isoformat()}")
    return len(days)


def _needs_lunch(it):
    text = " " + " ".join([it.get("title") or "", " ".join(it.get("bring") or []), it.get("details") or ""]).lower() + " "
    return any(w in text for w in LUNCH_WORDS)


def tick(now=None):
    """Kjøres hvert minutt. Hvert varsel sendes bare én gang (merkes i tabellen «sent»)."""
    now = now or util.now()
    s = db.all_settings()
    t = (now.hour, now.minute)

    eh, em = util.hm(s.get("evening_time"), "19:00")
    if (eh, em) <= t < (eh + 2, em):
        if s.get("evening_notify") and db.mark_sent(f"evening:{now.date().isoformat()}"):
            _evening(now, s)
        if s.get("deadline_notify") and db.mark_sent(f"deadlines:{now.date().isoformat()}"):
            _deadlines(now, s)

    rh, rm = util.hm(s.get("review_time"), "18:00")
    if now.weekday() == int(s.get("review_weekday", 6)) and (rh, rm) <= t < (rh + 4, rm):
        if db.mark_sent(f"review:{now.date().isoformat()}"):
            _review(now, s)

    if now.minute % 15 == 0:
        try:
            shopping_check(now)
        except Exception as e:
            log.warning("Handleliste: %s", e)
