"""Butikkvarsel: en Home Assistant-automasjon som varsler når noen er i en butikk
og det står noe på handlelista. Trykk på varselet åpner handlelista.

Automasjonen opprettes via HA sitt konfigurasjons-API og er AV til den slås på i appen.
"""
import json

import db
import ha

AUTOMATION_ID = "matplan_butikkvarsel"

# Ingen butikker som standard – legg inn deres egne under Mer → Innstillinger → Butikkvarsel.
DEFAULT_STORES = []

DEFAULTS = {
    "stores": DEFAULT_STORES,
    "people": [],          # [{person: "person.ola", notify: "notify.mobile_app_..."}]
    "notify_all": False,   # varsle alle, ikke bare den som er i butikken
    "from": "07:00",
    "to": "22:00",
    "minutes": 2,          # må ha vært i butikken så lenge (unngår varsel når man kjører forbi)
    "link": "todo",        # «todo» = handlelista i HA, «matplan» = Matplan-appen
}


def settings():
    s = db.get_setting("store_alert") or {}
    return {**DEFAULTS, **s}


def save(data):
    s = settings()
    for k in DEFAULTS:
        if k in data:
            s[k] = data[k]
    db.set_setting("store_alert", s)
    return s


def entities():
    """Personer og varslingstjenester (mobilapper) i Home Assistant."""
    persons = []
    for st in ha.states():
        if st["entity_id"].startswith("person."):
            persons.append({"entity_id": st["entity_id"], "name": st["attributes"].get("friendly_name", st["entity_id"])})
    notifies = []
    try:
        for dom in ha._req("GET", "/services") or []:
            if dom.get("domain") == "notify":
                for svc in (dom.get("services") or {}):
                    if svc.startswith("mobile_app_"):
                        notifies.append("notify." + svc)
    except Exception:
        pass
    return persons, sorted(notifies)


def guess_people(persons, notifies):
    """Første gang: koble person ↔ telefon ut fra navnet (ola → mobile_app_olas_telefon)."""
    out = []
    for p in persons:
        key = p["entity_id"].split(".", 1)[1].split("_")[0].lower()
        match = next((n for n in notifies if key and key in n.lower()), "")
        out.append({"person": p["entity_id"], "notify": match})
    return out


def build(s, todo_entity, matplan_path=None):
    link = f"/todo?entity_id={todo_entity}" if s.get("link") != "matplan" or not matplan_path else matplan_path
    people = [p for p in s.get("people") or [] if p.get("person") and p.get("notify")]
    triggers = []
    for pi, p in enumerate(people):
        for si, st in enumerate(s.get("stores") or []):
            lat, lon, km = float(st["lat"]), float(st["lon"]), float(st.get("radius") or 120) / 1000
            ent = p["person"]
            triggers.append({
                "trigger": "template",
                "value_template": (
                    f"{{% set d = distance({lat}, {lon}, '{ent}') %}}{{{{ d is number and d < {km} }}}}"
                ),
                "for": {"minutes": int(s.get("minutes") or 2)},
                "id": f"{pi}-{si}",
                "variables": {"person": ent, "notify_to": p["notify"], "store": st["name"]},
            })
    all_notify = sorted({p["notify"] for p in people})
    return {
        "alias": "Matplan – butikkvarsel",
        "description": "Laget av Matplan. Varsler når noen er i en av butikkene og det står noe på handlelista. "
                       "Endre butikker og personer i Matplan → Mer → Innstillinger.",
        "mode": "queued",
        "max": 10,
        "triggers": triggers,
        "conditions": [
            {"condition": "time", "after": f"{s.get('from') or '07:00'}:00", "before": f"{s.get('to') or '22:00'}:00"},
            {"condition": "template", "value_template": f"{{{{ states('{todo_entity}') | int(0) > 0 }}}}"},
            {"condition": "template", "value_template": "{{ not is_state(person, 'home') }}"},
        ],
        "actions": [
            {"action": "todo.get_items", "target": {"entity_id": todo_entity},
             "data": {"status": "needs_action"}, "response_variable": "liste"},
            {"variables": {
                "varer": f"{{{{ liste['{todo_entity}']['items'] | map(attribute='summary') | list }}}}",
                "navn": "{{ state_attr(person, 'friendly_name') or 'Noen' }}",
                "mottakere": json.dumps(all_notify) if s.get("notify_all") else "{{ [notify_to] }}",
            }},
            {"repeat": {"for_each": "{{ mottakere }}", "sequence": [{
                "action": "{{ repeat.item }}",
                "data": {
                    "title": "🛒 {{ varer | count }} på handlelista – {{ store }}",
                    "message": ("{% if notify_to != repeat.item %}{{ navn }} er på {{ store }}. {% endif %}"
                                "{{ varer[:6] | join(', ') }}{% if varer | count > 6 %} og {{ varer | count - 6 }} til{% endif %}"),
                    "data": {"url": link, "clickAction": link, "group": "matplan", "tag": "matplan-handleliste"},
                },
            }]}},
        ],
    }


def _automation_entity():
    for st in ha.states():
        if st["entity_id"].startswith("automation.") and st["attributes"].get("id") == AUTOMATION_ID:
            return st
    return None


def status():
    st = _automation_entity()
    return {"installed": bool(st), "on": bool(st and st["state"] == "on"),
            "entity_id": st["entity_id"] if st else None}


def install(todo_entity, matplan_path=None, turn_on=False):
    """Oppretter/oppdaterer automasjonen. Den er AV med mindre turn_on=True."""
    s = settings()
    if not s.get("people"):
        persons, notifies = entities()
        s["people"] = guess_people(persons, notifies)
        save({"people": s["people"]})
    cfg = build(s, todo_entity, matplan_path)
    if not cfg["triggers"]:
        raise RuntimeError("Velg minst én person med telefon og én butikk først")
    was = status()
    ha._req("POST", f"/config/automation/config/{AUTOMATION_ID}", cfg)
    # vent til HA har lastet automasjonen, og sett riktig på/av
    import time
    for _ in range(10):
        st = _automation_entity()
        if st:
            break
        time.sleep(0.5)
    st = _automation_entity()
    if st:
        want_on = turn_on or (was["installed"] and was["on"])
        ha.call_service("automation", "turn_on" if want_on else "turn_off", {"entity_id": st["entity_id"]})
    return status()


def set_on(on):
    st = _automation_entity()
    if not st:
        raise RuntimeError("Automasjonen er ikke opprettet ennå")
    ha.call_service("automation", "turn_on" if on else "turn_off", {"entity_id": st["entity_id"]})
    return status()
