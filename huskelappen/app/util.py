"""Små hjelpere: tid, tekst og JSON fra AI-svar."""
import difflib
import html
import json
import os
import re
from datetime import datetime

import ha

_tz = None

WEEKDAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
MONTHS = ["januar", "februar", "mars", "april", "mai", "juni", "juli", "august",
          "september", "oktober", "november", "desember"]


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


WEEKDAYS_SHORT = ["man", "tir", "ons", "tor", "fre", "lør", "søn"]


def norm(s):
    return re.sub(r"[^a-zæøå0-9]+", " ", (s or "").lower()).strip()


def similar(a, b, limit=0.8):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() > limit


# Ord som ikke sier noe om hvilken hendelse det er
_GENERIC = {"barnehagen", "barnehage", "barna", "barn", "alle", "stengt", "stenger", "dagen", "denne",
            "husk", "huske", "ha", "med", "til", "for", "kl", "og", "på", "i", "er", "det", "som", "om",
            "fra", "være", "her", "senest", "skolen", "klassen", "avdelingen", "hele"}


def _stem(w):
    """Fjerner vanlige bøyningsendelser: «planleggingsdagen» → «planleggingsdag»."""
    for end in ("ene", "ane", "en", "et", "er", "a"):
        if w.endswith(end) and len(w) - len(end) >= 4:
            return w[:-len(end)]
    return w


def sig_words(title):
    """Ordene som sier hva hendelsen er (uten bøyning, så «planleggingsdagen» = «planleggingsdag»)."""
    return {_stem(w) for w in norm(re.sub(r"\d", " ", title or "")).split()
            if len(w) >= 4 and w not in _GENERIC}


def strip_icon(s):
    return re.sub(r"^[^\wæøåÆØÅ]+", "", s or "").strip()


def same_event(a, b, kind_a=None, kind_b=None):
    """Er to titler (samme dag) trolig samme hendelse, bare formulert ulikt?"""
    a, b = strip_icon(a), strip_icon(b)
    if similar(a, b, 0.85):
        return True
    if kind_a == "stengt" and kind_b == "stengt":
        return True  # bare én stengt-dag per dag
    if kind_a and kind_b and (kind_a == "ha_med") != (kind_b == "ha_med"):
        return False  # «ha med matpakke» og selve hendelsen er to ulike påminnelser
    wa, wb = sig_words(a), sig_words(b)
    if not wa or not wb:
        return False
    common = wa & wb
    if not common:
        return False
    small = wa if len(wa) <= len(wb) else wb
    return common == small or len(common) / len(wa | wb) >= 0.5


def weekdays_text(wds):
    """[1] → «tirsdager», [0,1,2,3,4] → «alle hverdager»."""
    wds = sorted(set(int(w) for w in wds or []))
    if wds == [0, 1, 2, 3, 4]:
        return "alle hverdager"
    if wds == list(range(7)):
        return "hver dag"
    names = [WEEKDAYS[w] + "er" for w in wds]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " og " + names[-1]


def nice_date(d):
    """«fredag 6. november»"""
    return f"{WEEKDAYS[d.weekday()]} {d.day}. {MONTHS[d.month - 1]}"


def hm(s, default):
    try:
        h, m = str(s or default).split(":")[:2]
        return int(h), int(m)
    except Exception:
        h, m = default.split(":")
        return int(h), int(m)


def html_to_text(s):
    s = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|table)>", "\n", s)
    s = re.sub(r"(?i)</t[dh]>", " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def extract_json(text):
    """Henter JSON ut av et modellsvar (tåler ```-blokker og tekst rundt)."""
    if isinstance(text, (dict, list)):
        return text
    t = str(text).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.M).strip()
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        raise ValueError("Fant ikke JSON i AI-svaret")
    start = min(starts)
    closer = "}" if t[start] == "{" else "]"
    chunk = t[start:t.rfind(closer) + 1]
    try:
        return json.loads(chunk)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", chunk))
