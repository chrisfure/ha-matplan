"""Henter kvitteringer fra Trumf (Kiwi, Meny, Spar, Joker) – KUN lesing.

Trumf har ikke noe åpent API. Kvitteringene ligger på de innloggede sidene på
www.trumf.no, og Matplan leser dem derfra med innloggingskaken (cookie) fra en
nettleser der du selv har logget inn. Matplan håndterer aldri passord eller BankID.

Sikkerhet:
  - Klienten gjør bare GET mot www.trumf.no/trumf-profil/kvitteringer[/<id>].
  - Kaken sendes aldri til andre adresser: viderekoblinger bort fra www.trumf.no
    følges ikke (de betyr i praksis at innloggingen er utløpt).

Sideformatet (Next.js «flight»-data med nøklene transactionsData og purchaseDetails)
er beskrevet i github.com/fredrsat/trumf-connector. Det er uoffisielt og kan endre
seg uten varsel – da stopper hentingen med en feilmelding, og ingenting annet påvirkes.
"""
import hashlib
import http.cookies
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import db
import logic
from config import DATA_DIR, load_options

log = logging.getLogger("matplan.trumf")

HOST = "www.trumf.no"
BASE = "https://" + HOST
LIST_PATH = "/trumf-profil/kvitteringer"
SLOTS = (1, 2)          # to Trumf-kontoer, f.eks. én per voksen
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
INTERVAL_HOURS = 6
PAUSE_SECONDS = 1.5     # pause mellom kvitteringer, så vi ikke belaster Trumf
MAX_PER_RUN = 150

# Kjedenavn slik resten av Matplan skriver dem (samme som AI-tolkingen bruker)
CHAINS = {"kiwi": "Kiwi", "meny": "Meny", "spar": "Spar", "eurospar": "Spar", "joker": "Joker",
          "nærbutikken": "Nærbutikken", "bunnpris": "Bunnpris"}
UNITS = {"ea": "stk", "stk": "stk", "kg": "kg", "kgm": "kg", "l": "l", "ltr": "l"}

_lock = threading.Lock()
status = {"last_run": None, "last_result": None, "error": None, "accounts": {}}


class TrumfError(Exception):
    pass


class NotLoggedIn(TrumfError):
    def __init__(self):
        super().__init__(
            "Innloggingen hos Trumf er utløpt eller ugyldig. Logg inn på trumf.no i en nettleser, "
            "kopier Cookie-verdien på nytt og lim den inn i app-innstillingene (se bruksanvisningen)."
        )


# --- Innloggingskake ----------------------------------------------------------

def _cookie_file(slot):
    return os.path.join(DATA_DIR, "trumf_cookies.json" if slot == 1 else f"trumf_cookies_{slot}.json")


def _option_cookie(slot=1):
    key = "trumf_cookie" if slot == 1 else f"trumf_cookie_{slot}"
    raw = str(load_options().get(key) or "").strip()
    raw = re.sub(r"^cookie:\s*", "", raw, flags=re.I)
    return " ".join(raw.split())


def configured(slot):
    return "session-token" in _option_cookie(slot)


def accounts():
    """Kontoene som er satt opp, som liste med kontonummer."""
    return [s for s in SLOTS if configured(s)]


def enabled():
    return bool(accounts())


def public_status():
    """Status uten hemmeligheter, til visning i appen."""
    return {**{k: v for k, v in status.items() if k != "accounts"}, "enabled": enabled(),
            "accounts": [{"slot": s, "configured": configured(s), **status["accounts"].get(s, {})} for s in SLOTS]}


def _parse_cookie_header(raw):
    out = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def _load_cookies(slot=1):
    """Kakene fra innstillingene, oppdatert med det Trumf har fornyet underveis.
    Limes en ny verdi inn i innstillingene, starter vi på nytt fra den.
    Returnerer (nøkkel, kaker) der nøkkelen sier hvilken konto og hvilken innlimt verdi det gjelder."""
    raw = _option_cookie(slot)
    sig = f"{slot}:" + hashlib.sha256(raw.encode()).hexdigest()
    try:
        with open(_cookie_file(slot), encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("sig") == sig and isinstance(saved.get("cookies"), dict):
            return sig, saved["cookies"]
    except Exception:
        pass
    return sig, _parse_cookie_header(raw)


def _save_cookies(sig, cookies):
    try:
        path = _cookie_file(int(sig.split(":", 1)[0]))
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"sig": sig, "cookies": cookies}, f)
        os.chmod(path, 0o600)
    except Exception as e:
        log.warning("Kunne ikke lagre Trumf-kaker: %s", e)


class _NoForeignRedirect(urllib.request.HTTPRedirectHandler):
    """Følger aldri viderekoblinger – kaken skal ikke kunne havne hos en annen vert."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoForeignRedirect())


def _fetch(path, sig, cookies, hops=3):
    if not (path == LIST_PATH or path.startswith(LIST_PATH + "/")):
        raise TrumfError(f"Sperret: Matplan har ikke lov til å hente {path} hos Trumf")
    req = urllib.request.Request(BASE + path, method="GET", headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
    })
    try:
        with _opener.open(req, timeout=40) as r:
            body = r.read().decode("utf-8", "replace")
            headers = r.headers
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            target = urllib.parse.urlparse(urllib.parse.urljoin(BASE + path, e.headers.get("Location") or ""))
            # Samme vert og fortsatt under kvitteringssidene (f.eks. skråstrek til slutt) er greit.
            if hops > 0 and f"{target.scheme}://{target.netloc}" == BASE and \
                    (target.path.rstrip("/") == path.rstrip("/")) and target.path != path:
                return _fetch(target.path, sig, cookies, hops - 1)
            raise NotLoggedIn() from None
        if e.code in (401, 403):
            raise NotLoggedIn() from None
        raise TrumfError(f"Trumf svarte med feil {e.code}") from None
    except urllib.error.URLError as e:
        raise TrumfError(f"Får ikke kontakt med Trumf: {e.reason}") from None
    # Trumf fornyer innloggingen underveis – ta vare på nye kaker så den varer lenger
    changed = False
    for line in headers.get_all("Set-Cookie") or []:
        try:
            jar = http.cookies.SimpleCookie()
            jar.load(line)
        except Exception:
            continue
        for name, morsel in jar.items():
            if morsel.value == "" or morsel["max-age"] == "0":
                changed = cookies.pop(name, None) is not None or changed
            elif cookies.get(name) != morsel.value:
                cookies[name] = morsel.value
                changed = True
    if changed:
        _save_cookies(sig, cookies)
    return body


# --- Tolking av sidene ----------------------------------------------------------

_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')


def _flight_text(page):
    """Slår sammen bitene Next.js deler de innebygde dataene i (en verdi kan gå over flere biter)."""
    chunks = _CHUNK.findall(page)
    return "".join(chunks) if chunks else page


def extract_json_after(page, key):
    """Finner JSON-verdien som følger nøkkelen i sidens innebygde data.

    Dataene ligger som en JavaScript-streng i HTML-en, så hermetegn står som \\".
    Vi leter fram hele lista/objektet (tar hensyn til tekst med klammer i),
    gjør om til vanlig JSON og tolker den. Returnerer None hvis nøkkelen mangler.
    """
    text = _flight_text(page)
    marker = key + '\\":'
    at = text.find(marker)
    if at < 0:
        return None
    i = at + len(marker)
    while i < len(text) and text[i] not in "[{":
        if text[i] not in " \n\t":
            return None  # verdien er ikke en liste/et objekt (f.eks. en henvisning)
        i += 1
    if i >= len(text):
        return None
    opener = text[i]
    closer = "]" if opener == "[" else "}"
    depth, in_str, j, n = 0, False, i, len(text)
    done = False
    while j < n:
        c = text[j]
        if c == "\\":
            run = 0
            while j + run < n and text[j + run] == "\\":
                run += 1
            if j + run < n and text[j + run] == '"':
                if run % 2 == 0:
                    break  # uescapet hermetegn = slutten på JavaScript-strengen
                # \" er et ekte hermetegn i JSON-en, \\\" er et hermetegn inne i en tekst
                if run % 4 == 1:
                    in_str = not in_str
                j += run + 1
            else:
                j += run
            continue
        if c == '"':
            break  # slutten på JavaScript-strengen – dataene er avkuttet
        if not in_str:
            if c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    j += 1
                    done = True
                    break
        j += 1
    if not done:
        return None
    try:
        inner = json.loads('"' + text[i:j] + '"')  # fjerner det ytre laget med skråstreker
        return json.loads(inner)
    except ValueError:
        return None


def _clean(v):
    """Next.js skriver datoer som «$D2026-…» og manglende verdier som «$undefined»."""
    if isinstance(v, str):
        if v == "$undefined":
            return None
        return v[2:] if v.startswith("$D") else v
    return v


def _num(v):
    v = _clean(v)
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_transactions(page):
    data = extract_json_after(page, "transactionsData")
    if not isinstance(data, list):
        return None
    return [t for t in data if isinstance(t, dict) and _clean(t.get("batchId"))]


def parse_receipt(page):
    data = extract_json_after(page, "purchaseDetails")
    return data if isinstance(data, dict) else None


def _local_date(ts):
    ts = _clean(ts)
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        m = re.match(r"\d{4}-\d{2}-\d{2}", str(ts))
        return m.group(0) if m else None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(logic.tz()).date().isoformat()


def chain_name(tx):
    desc = str(_clean(tx.get("beskrivelse")) or "").strip()
    for cand in (_clean(tx.get("filterCategory")), _clean(tx.get("partnerId")), desc.split(" ")[0] if desc else ""):
        k = str(cand or "").strip().lower()
        if k in CHAINS:
            return CHAINS[k]
    first = desc.split(" ")[0] if desc else ""
    return first.capitalize() if first else "Trumf"


def to_receipt(tx, details):
    """Gjør én Trumf-kvittering om til formatet logic.save_receipt forventer. None = ingen varer."""
    lines = []
    for ln in details.get("varelinjer") or []:
        if not isinstance(ln, dict):
            continue
        name = str(_clean(ln.get("produktBeskrivelse")) or "").strip()
        amount = _num(ln.get("belop"))
        if not name or amount is None:
            continue
        saved = sum(_num(s.get("belop")) or 0 for s in (ln.get("besparelser") or []) if isinstance(s, dict))
        qty = _num(ln.get("antall"))
        lines.append({
            "navn": name,
            "antall": qty if qty and qty > 0 else 1,
            "enhet": UNITS.get(str(_clean(ln.get("enhetsType")) or "").lower(), "stk"),
            "belop": amount,
            "rabatt": saved,
        })
    if not lines:
        return None
    total = _num(details.get("belop"))
    if total is None:
        total = _num(tx.get("belop"))
    gross = sum(l["belop"] for l in lines)
    net = gross - sum(l["rabatt"] for l in lines)
    # Vi vet ikke sikkert om linjebeløpet er før eller etter rabatt. Den varianten som
    # summerer seg nærmest kvitteringens totalbeløp, er den riktige.
    subtract = total is not None and abs(net - total) + 0.005 < abs(gross - total)
    batch = str(_clean(details.get("batchId")) or _clean(tx.get("batchId")))
    return {
        "butikk": chain_name(tx),
        "dato": _local_date(details.get("transaksjonsTidspunkt")) or _local_date(tx.get("transaksjonsTidspunkt")),
        "total": total if total is not None else round(net, 2),
        "ref": "trumf:" + batch,
        "varer": [{
            "navn": l["navn"], "antall": l["antall"], "enhet": l["enhet"],
            "pris": round(l["belop"] - l["rabatt"], 2) if subtract else l["belop"],
        } for l in lines],
    }


# --- Henting ------------------------------------------------------------------

def poll(progress=None):
    if not _lock.acquire(blocking=False):
        return "Henting pågår allerede"
    try:
        return _poll(progress)
    finally:
        _lock.release()


def _poll(progress):
    slots = accounts()
    if not slots:
        return "Trumf er ikke satt opp (lim inn Cookie-verdien i app-innstillingene i Home Assistant)"
    status["last_run"] = logic.now().isoformat(timespec="minutes")
    results, errors, saved_total = [], [], 0
    for slot in slots:
        label = f"Konto {slot}: " if len(slots) > 1 else ""
        try:
            saved, text = _poll_account(slot, progress, label)
            saved_total += saved
            status["accounts"][slot] = {"last_result": text, "error": None}
            results.append(label + text)
        except Exception as e:
            log.warning("Trumf-henting feilet (konto %s): %s", slot, e)
            status["accounts"][slot] = {"last_result": None, "error": str(e)}
            errors.append(label + str(e))
    if saved_total:
        logic.normalize_pending(progress)
    status.update(last_result="; ".join(results) or None, error="; ".join(errors) or None)
    if errors and not results:
        raise TrumfError("; ".join(errors))
    return "; ".join(results + ["Feil – " + e for e in errors])


def _poll_account(slot, progress, label=""):
    """Henter nye kvitteringer for én konto. Returnerer (antall lagret, tekst)."""
    sig, cookies = _load_cookies(slot)
    if progress:
        progress(f"{label}Henter kjøpslista fra Trumf")
    page = _fetch(LIST_PATH, sig, cookies)
    txs = parse_transactions(page)
    if txs is None:
        if "transactionsData" in page:
            raise TrumfError("Trumf har endret sideformatet – Matplan klarer ikke å lese kjøpslista lenger")
        raise NotLoggedIn()
    since = (logic.today() - timedelta(days=int(load_options().get("trumf_since_days") or 365))).isoformat()
    # Leses på nytt for hver konto: to kontoer i samme husstand kan vise de samme kjøpene
    seen = {r["msgid"] for r in db.q("SELECT msgid FROM mail_seen WHERE msgid LIKE 'trumf:%'")}
    seen |= {r["source_ref"] for r in db.q("SELECT source_ref FROM receipts WHERE source_ref LIKE 'trumf:%'")}
    todo = []
    for tx in txs:
        ref = "trumf:" + str(_clean(tx.get("batchId")))
        d = _local_date(tx.get("transaksjonsTidspunkt"))
        if ref in seen or _clean(tx.get("harKvittering")) is False or (d and d < since):
            continue
        todo.append((ref, tx))
    todo = todo[:MAX_PER_RUN]
    by = "Trumf" if slot == 1 else f"Trumf (konto {slot})"
    saved = skipped = failed = 0
    for idx, (ref, tx) in enumerate(todo):
        if progress:
            progress(f"{label}Leser Trumf-kvittering {idx + 1} av {len(todo)}")
        if idx:
            time.sleep(PAUSE_SECONDS)
        try:
            batch = str(_clean(tx.get("batchId")))
            detail_page = _fetch(f"{LIST_PATH}/{urllib.parse.quote(batch, safe='')}", sig, cookies)
            details = parse_receipt(detail_page)
            if details is None:
                raise TrumfError("fant ikke kvitteringsdata på siden")
            data = to_receipt(tx, details)
            rid, res = None, "Ingen varelinjer"
            if data is not None:
                rid, res = logic.save_receipt(data, "trumf", data["ref"], by, logic.today())
        except NotLoggedIn:
            raise
        except Exception as e:
            # prøves igjen neste runde
            log.warning("Trumf-kvittering %s feilet: %s", ref, e)
            failed += 1
            continue
        if rid:
            saved += 1
        else:
            skipped += 1
        db.ex("INSERT OR REPLACE INTO mail_seen(msgid, result) VALUES(?,?)", (ref, res))
        log.info("Trumf %s: %s", ref, res)
    result = "1 ny kvittering fra Trumf" if saved == 1 else f"{saved} nye kvitteringer fra Trumf"
    if skipped:
        result += f", {skipped} hoppet over (fantes fra før eller uten varer)"
    if failed:
        result += f", {failed} feilet (prøves igjen)"
    return saved, result


def start_scheduler():
    def loop():
        time.sleep(45)
        while True:
            if enabled():
                try:
                    poll()
                except Exception:
                    pass
            time.sleep(INTERVAL_HOURS * 3600)

    threading.Thread(target=loop, daemon=True, name="trumf-poller").start()
