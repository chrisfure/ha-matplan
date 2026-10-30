"""Tolker e-post, PDF-er og bilder av lapper med Claude (via Home Assistant sin AI-oppgave)."""
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import date

import db
import ha
import logic
import util
from config import MEDIA_DIR, MEDIA_SOURCE_PREFIX

log = logging.getLogger("huskelappen.extract")

KINDS = {"ha_med", "spesiell_dag", "foreldre", "frist", "stengt", "info", "fast"}
MAX_IMAGES = 6


# --- PDF ----------------------------------------------------------------------

def pdf_text(content):
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(content)
        f.flush()
        try:
            out = subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, timeout=60)
            return out.stdout.decode("utf-8", "replace")
        except Exception:
            return ""


def pdf_images(content, max_pages=3, dpi=110):
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in.pdf")
        with open(src, "wb") as f:
            f.write(content)
        subprocess.run(["pdftoppm", "-jpeg", "-r", str(dpi), "-f", "1", "-l", str(max_pages),
                        src, os.path.join(d, "p")], capture_output=True, timeout=120)
        out = []
        for name in sorted(os.listdir(d)):
            if name.startswith("p") and name.endswith(".jpg"):
                with open(os.path.join(d, name), "rb") as f:
                    out.append(f.read())
        return out


def is_pdf(name, data):
    return data[:5] == b"%PDF-" or (name or "").lower().endswith(".pdf")


def image_type(name, data):
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


def _save_image(content, ext="jpg"):
    os.makedirs(MEDIA_DIR, exist_ok=True)
    fname = hashlib.sha256(content).hexdigest()[:20] + "." + ext
    path = os.path.join(MEDIA_DIR, fname)
    with open(path, "wb") as f:
        f.write(content)
    mime = "image/png" if ext == "png" else "image/jpeg"
    return path, {"media_content_id": f"{MEDIA_SOURCE_PREFIX}/{fname}", "media_content_type": mime}


# --- Prompt -------------------------------------------------------------------

def children_text(s):
    lines = []
    for c in s.get("children") or []:
        groups = ", ".join(c.get("groups") or []) or "ukjent"
        label = c.get("label") or c["name"]
        lines.append(f"- {c['name']}: skrives «{label}». Avdeling/klasse/grupper: {groups}.")
    return "\n".join(lines)


def build_prompt(s, subject, sender, received, body, pdfs, today=None, source_name=None):
    today = today or util.today()
    names = [c["name"] for c in s.get("children") or []]
    where = source_name or s.get("kindergarten") or "barnehagen"
    parts = [f"""Du hjelper en familie med å huske ting fra barnehage, skole og aktiviteter. Denne gangen: {where}.
I dag er {util.nice_date(today)} {today.isoformat()}. Skrivet kom {util.nice_date(received)} {received.isoformat()}.

Familiens barn som dette kan gjelde:
{children_text(s)}

Les e-posten og vedleggene (ukeplan, tilbakeblikk, infoskriv eller bilde av en lapp – som tekst og/eller bilder).
Finn KUN det foreldrene må gjøre noe med eller huske:
- ha_med: noe barna skal ha med (klær, sekk, matpakke, drikkeflaske, sitteunderlag, kostyme, leke osv.)
- spesiell_dag: dager som avviker fra vanlig rutine (tur/turdag, utflukt, fotografering, fest, besøk ut)
- stengt: barnehagen stengt, planleggingsdag, tidlig stengning
- foreldre: noe foreldrene skal delta på (foreldresamtale for familiens barn, foreldremøte, dugnad, kafé, temakveld)
- frist: påmelding, svar eller betaling innen en dato
- info: annen viktig praktisk beskjed (engangs)
- fast: noe som gjentar seg HVER uke på bestemte ukedager (f.eks. «gymtøy hver tirsdag», «Ha med-dag hver dag»).
  Bruk «ukedager» (1 = mandag … 7 = søndag) og sett dato til første dag det gjelder.

Ikke ta med:
- personalmøter, ped.leder-møter, møtedager for andre avdelinger, plantid og annet som bare gjelder de ansatte
- vanlige faste aktiviteter (samling, utelek, grupper, yoga, klubb, måltider barnehagen serverer) når ingenting skal med
- andre barns foreldresamtaler (bare samtaler der barnets navn passer med skrivemåten over)
- tilbakeblikk på det som har skjedd, takk, hilsener og gratulasjoner

Hvem det gjelder («gjelder»), bruk bare disse navnene: {json.dumps(names, ensure_ascii=False)}
- noe for hele barnehagen/skolen, avdelingen eller klassen: alle barna der
- noe for en klubb eller gruppe: bare barna i den gruppen
- er du usikker: alle barna i avdelingen

Datoer:
- Bruk datoene som står i ukeplanens kolonner. Overskrifter kan være feil (f.eks. en måltidstabell med feil ukenummer) – stol på datoene ved dagene.
- Står bare ukedag, regn ut datoen ut fra uka skrivet gjelder eller datoen e-posten kom.
- Ta ikke med noe som skjer før {received.isoformat()}.

Klokkeslett:
- start/slutt bare for avtaler foreldrene møter på (samtale, møte, arrangement). Ellers null.
- For barnas ting (f.eks. tur) skrives viktig klokkeslett i tittelen: «Turdag – går kl. 09.30».

Returner KUN JSON:
{{"punkter":[{{"type":"ha_med|spesiell_dag|stengt|foreldre|frist|info|fast","dato":"YYYY-MM-DD","til_dato":null,"ukedager":null,"start":null,"slutt":null,"tittel":"kort, maks 40 tegn","ha_med":["..."],"detaljer":"én kort setning","gjelder":["navn"],"kilde":"kort sitat fra skrivet, maks 15 ord"}}]}}
Finnes ingenting: {{"punkter":[]}}

E-post
Fra: {sender}
Emne: {subject}
\"\"\"
{body[:12000]}
\"\"\""""]
    for name, text in pdfs:
        parts.append(f"Vedlegg «{name}» (tekst):\n\"\"\"\n{text[:12000]}\n\"\"\"")
    return "\n\n".join(parts)


def run_ai(prompt, attachments):
    entity = db.get_setting("ai_entity") or None
    try:
        raw = ha.ai_task(prompt, entity_id=entity, attachments=attachments or None,
                         task_name="Huskelappen: les barnehageskriv")
    except ha.HAError as e:
        if not attachments:
            raise
        # noen AI-entiteter tar ikke bilder – prøv med bare tekst
        log.warning("AI med vedlegg feilet (%s) – prøver med bare tekst", e)
        raw = ha.ai_task(prompt, entity_id=entity, task_name="Huskelappen: les barnehageskriv")
    return util.extract_json(raw)


def read_mail(subject, sender, received, body, attachments, s=None, source_name=None):
    """attachments: liste av (filnavn, bytes) – PDF eller bilde. Returnerer rå punkter fra AI."""
    s = s or db.all_settings()
    pdfs, images, paths = [], [], []

    def add_image(data, ext="jpg"):
        if len(images) < MAX_IMAGES:
            path, att = _save_image(data, ext)
            paths.append(path)
            images.append(att)
    try:
        for name, data in attachments:
            if is_pdf(name, data):
                pdfs.append((name, pdf_text(data)))
                for img in pdf_images(data):
                    add_image(img)
            elif image_type(name, data):
                add_image(data, image_type(name, data))
        if not body.strip() and not pdfs and not images:
            return []
        prompt = build_prompt(s, subject, sender, received, body, pdfs, source_name=source_name)
        data = run_ai(prompt, images)
    finally:
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass
    return (data.get("punkter") or []) if isinstance(data, dict) else []


# --- Rydding og lagring -------------------------------------------------------

_norm = util.norm


def _time(v):
    m = re.match(r"^\s*(\d{1,2})[:.](\d{2})", str(v or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _day(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def clean(points, s, today=None):
    today = today or util.today()
    by_name = {}
    for c in s.get("children") or []:
        for key in (c["name"], c.get("label") or ""):
            if key:
                by_name[_norm(key)] = c["name"]
    all_names = [c["name"] for c in s.get("children") or []]
    out = []
    for p in points:
        if not isinstance(p, dict):
            continue
        d = _day(p.get("dato"))
        if not d or d < today:
            continue
        end = _day(p.get("til_dato"))
        if end and end <= d:
            end = None
        kids = []
        given = [n for n in (p.get("gjelder") or []) if str(n).strip()]
        for n in given:
            name = by_name.get(_norm(n))
            if name and name not in kids:
                kids.append(name)
        if given and not kids:
            continue  # gjelder et annet barn med samme fornavn
        kind = p.get("type") if p.get("type") in KINDS else "info"
        wds = []
        for w in p.get("ukedager") or []:
            try:
                w = int(w)
            except (TypeError, ValueError):
                continue
            if 1 <= w <= 7:
                wds.append(w - 1)
        wds = sorted(set(wds))
        if kind == "fast" and not wds:
            wds = [d.weekday()]
        title = (p.get("tittel") or "").strip()[:60] or kind.replace("_", " ").capitalize()
        bring = [str(b).strip() for b in (p.get("ha_med") or []) if str(b).strip()]
        out.append({
            "day": d.isoformat(),
            "end_day": end.isoformat() if end else None,
            "start_time": _time(p.get("start")),
            "end_time": _time(p.get("slutt")),
            "title": title,
            "bring": bring,
            "details": " ".join(x for x in [(p.get("detaljer") or "").strip(),
                                            f"«{p['kilde'].strip()}»" if p.get("kilde") else ""] if x),
            "children": [n for n in all_names if n in kids] or all_names,
            "kind": kind,
            "weekdays": wds if kind == "fast" else [],
        })
    return out


_similar = util.similar


def save(items, msgid, subject, source=None):
    """Lagrer nye forslag. Hopper over det som allerede finnes. Returnerer de nye."""
    new = []
    for it in items:
        wds = it.get("weekdays") or [date.fromisoformat(it["day"]).weekday()]
        if logic.fixed_covers(it["title"], wds):
            continue  # dekkes allerede av en fast påminnelse
        if it["kind"] == "fast" and any(
                r["kind"] == "fast" and r["status"] != "rejected" and _similar(r["title"], it["title"])
                for r in db.q("SELECT kind, status, title FROM items WHERE kind='fast'")):
            continue
        key = f"{it['day']}|{_norm(it['title'])}|{','.join(sorted(it['children']))}"
        same_day = db.q("SELECT title, children FROM items WHERE day=?", (it["day"],))
        if any(_similar(r["title"], it["title"]) and set(json.loads(r["children"])) & set(it["children"])
               for r in same_day):
            continue
        try:
            iid = db.ex(
                "INSERT INTO items(msgid, day, end_day, start_time, end_time, title, bring, details, "
                "children, kind, dedupe_key, source_subject, source, weekdays) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (msgid, it["day"], it["end_day"], it["start_time"], it["end_time"], it["title"],
                 json.dumps(it["bring"], ensure_ascii=False), it["details"],
                 json.dumps(it["children"], ensure_ascii=False), it["kind"], key, subject, source,
                 json.dumps(it.get("weekdays") or [])),
            )
        except Exception:  # dedupe_key finnes fra før
            continue
        new.append({**it, "id": iid})
    return new
