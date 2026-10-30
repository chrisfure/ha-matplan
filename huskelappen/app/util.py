"""Små hjelpere: tid, tekst og JSON fra AI-svar."""
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
