"""Parsing av kvitteringstekst, e-post og AI-svar."""
import html
import json
import re
from datetime import date

MONTHS = {
    "januar": 1, "februar": 2, "mars": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "okt": 10, "nov": 11, "des": 12,
}


def amount(s):
    """'1 745,17' / '1745.17' / '-19,47' -> float"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).replace("\xa0", "").replace(" ", "").replace("kr", "").replace("NOK", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def no_date(text):
    """Finn første norske dato som '22. oktober 2026' eller '22.10.2026'."""
    m = re.search(r"(\d{1,2})\.\s*([a-zæøå]+)\.?\s+(\d{4})", text, re.I)
    if m and m.group(2).lower() in MONTHS:
        try:
            return date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b", text)
    if m:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return date(y, int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def norm_key(name):
    return re.sub(r"\s+", " ", (name or "").strip().lower())


# --- Oda --------------------------------------------------------------------

ODA_LINE = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*(stk|kg|g|l)\.?\s+(.+?)\s+MVA\s+\d+\s*%\s+(-?[\d\s.]*\d,\d{2})\s*NOK\s*$",
    re.I | re.M,
)


def parse_oda(text):
    """Leser en Oda-kvittering (e-post eller PDF-tekst). Returnerer dict eller None."""
    if not text or "Kvitteringsnummer" not in text or "Oda" not in text:
        return None
    m = re.search(r"Bestilte varer(.*?)(Oppsummering|Total inkl)", text, re.S)
    block = m.group(1) if m else text
    items = []
    for qty, unit, name, price in ODA_LINE.findall(block):
        items.append({
            "navn": name.strip(),
            "antall": amount(qty) or 1,
            "enhet": unit.lower(),
            "pris": amount(price),
        })
    if not items:
        return None
    ref = re.search(r"Kvitteringsnummer:\s*(\S+)", text)
    d = None
    fm = re.search(r"Fakturadato:?\s*([^\n]+)", text)
    if fm:
        d = no_date(fm.group(1))
    if not d:
        dm = re.search(r"Dato:?\s*([^\n]+)", text)
        d = no_date(dm.group(1)) if dm else None
    tot = re.search(r"Total inkl\.? MVA\s+([\d\s.]*\d,\d{2})", text)
    return {
        "butikk": "Oda",
        "dato": d.isoformat() if d else None,
        "total": amount(tot.group(1)) if tot else None,
        "varer": items,
        "ref": f"oda:{ref.group(1)}" if ref else None,
    }


# --- E-post / HTML ----------------------------------------------------------

def html_to_text(s):
    s = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|table)>", "\n", s)
    s = re.sub(r"(?i)</t[dh]>", " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def looks_like_receipt_text(text):
    if not text:
        return False
    prices = re.findall(r"\d+[.,]\d{2}\b", text)
    words = re.search(r"kvittering|sum|totalt?|mva|bank|kort", text, re.I)
    return len(prices) >= 5 and bool(words)


# --- AI-svar ----------------------------------------------------------------

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
    opener = t[start]
    closer = "}" if opener == "{" else "]"
    end = t.rfind(closer)
    chunk = t[start:end + 1]
    try:
        return json.loads(chunk)
    except json.JSONDecodeError:
        # vanlige småfeil: avsluttende komma
        fixed = re.sub(r",\s*([}\]])", r"\1", chunk)
        return json.loads(fixed)


# --- Pakningsstørrelse (uten AI) ---------------------------------------------

_UNIT = {"kg": ("kg", 1.0), "g": ("kg", 0.001), "gr": ("kg", 0.001), "gram": ("kg", 0.001), "hg": ("kg", 0.1),
         "l": ("l", 1.0), "ltr": ("l", 1.0), "liter": ("l", 1.0), "dl": ("l", 0.1), "cl": ("l", 0.01), "ml": ("l", 0.001)}
_NUM = r"(\d+(?:[.,]\d+)?)"
_MULTI = re.compile(_NUM + r"\s*[x×*]\s*" + _NUM + r"\s*(kg|gr|gram|g|hg|liter|ltr|l|dl|cl|ml)\b", re.I)
_SIZE = re.compile(r"(?<![\d%])" + _NUM + r"\s*(kg|gr|gram|g|hg|liter|ltr|l|dl|cl|ml)\b(?!\s*%)", re.I)
_COUNT = re.compile(r"(?<![\d\-])(\d{1,3})\s*(?:stk|stykk|pk|pakn|-pk|pack|p)\b(?!\s*-)", re.I)


def package_size(name):
    """Pakningsstørrelse fra et varenavn. Returnerer (mengde, enhet) der enhet er kg, l eller stk.

    «Bacon 150 g» → (0.15, "kg"), «Tine Lettmelk 1% fett, 1,75 l» → (1.75, "l"),
    «Yoghurt 4x125g» → (0.5, "kg"), «Egg 12 stk» → (12, "stk"). Ukjent → (None, None).
    """
    n = (name or "").replace("\xa0", " ")
    m = _MULTI.search(n)
    if m:
        cnt, amt, u = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", ".")), m.group(3).lower()
        basis, factor = _UNIT[u]
        val = cnt * amt * factor
        if val > 0:
            return round(val, 4), basis
    sizes = list(_SIZE.finditer(n))
    if sizes:
        m = sizes[-1]  # størrelsen står som regel til slutt
        amt, u = float(m.group(1).replace(",", ".")), m.group(2).lower()
        basis, factor = _UNIT[u]
        val = amt * factor
        if val > 0 and not (basis == "kg" and u in ("g", "gr") and amt < 5):  # «1 g» o.l. er sjelden en pakning
            return round(val, 4), basis
    if re.search(r"\d+\s*-\s*\d+\s*stk", n, re.I):
        return None, None  # «5-6 stk» – usikkert antall
    m = _COUNT.search(n)
    if m and int(m.group(1)) > 1:
        return float(m.group(1)), "stk"
    return None, None
