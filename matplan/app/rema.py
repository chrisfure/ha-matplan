"""Henter kvitteringer fra Rema 1000 (Æ-kontoen) – KUN lesing.

Rema har ikke noe åpent API for kvitteringer. Matplan bruker det samme grensesnittet
som Æ-appen, med en innlogging du gjør selv i nettleseren (mobilnummer + kode på SMS).
Matplan ser aldri mobilnummeret eller koden – den får bare et tilgangsbevis (token)
etterpå, som lagres i appens egen datamappe.

Sikkerhet – klienten kan bare:
  1. bytte innloggingskoden mot tilgangsbevis, og fornye beviset (POST til id.rema.no/token)
  2. lese kjøpslista og varelinjene (GET mot api.rema.no, kun stiene i ALLOWED_GET)
Det finnes ingen kode for tilbud, handlelister, betaling eller endring av kontoen.

Grensesnittet er uoffisielt og beskrevet i github.com/Alfredvc/rema1000-cli (API.md).
Det kan endre seg uten varsel – da stopper hentingen med en feilmelding.
"""
import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

import db
import logic
from config import DATA_DIR

log = logging.getLogger("matplan.rema")

AUTH_URL = "https://id.rema.no/authorization"
TOKEN_URL = "https://id.rema.no/token"
API_BASE = "https://api.rema.no"
# Verdiene under er bygget inn i Æ-appen og er like for alle – de er ikke personlige.
CLIENT_ID = "c886192e-2ac2-11e9-b210-d663bd873d93"
REDIRECT_URI = "https://ae-appen.appspot.com/redirect/redirect.html"
SUBSCRIPTION_KEY = "fb5e24884b504d0bad761098f77e6605"
APP_VERSION = "3.13.0.1181"

HEADS_PATH = "/v1/bella/transaction/v2/heads"
ROWS_PATH = "/v1/bella/transaction/v2/rows/"
ALLOWED_GET = (HEADS_PATH, ROWS_PATH)

TOKEN_FILE = os.path.join(DATA_DIR, "rema_tokens.json")
SLOTS = (1, 2)          # to Rema-kontoer, f.eks. én per voksen
INTERVAL_HOURS = 6
PAUSE_SECONDS = 1.0
MAX_PER_RUN = 150
SINCE_DAYS = 365
LOGIN_MINUTES = 15      # så lenge er en påbegynt innlogging gyldig

_lock = threading.Lock()
_file_lock = threading.RLock()
_pending = {}           # kontonummer → {"verifier", "state", "at"}
status = {"last_run": None, "last_result": None, "error": None, "accounts": {}}


class RemaError(Exception):
    rejected = False    # True når Rema avviste koden/beviset (ikke bare en forbigående feil)


class NotLoggedIn(RemaError):
    def __init__(self):
        super().__init__("Innloggingen hos Rema er utløpt. Koble til på nytt under Mer → Innstillinger → Butikk-kontoer.")


# --- Lagring av tilgangsbevis ---------------------------------------------------

def _load():
    with _file_lock:
        try:
            with open(TOKEN_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _save(data):
    with _file_lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = TOKEN_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, TOKEN_FILE)


def _account(slot):
    return _load().get(str(slot)) or None


def _set_account(slot, acc):
    with _file_lock:
        data = _load()
        if acc is None:
            data.pop(str(slot), None)
        else:
            data[str(slot)] = acc
        _save(data)


def connected(slot):
    acc = _account(slot)
    return bool(acc and acc.get("refresh_token"))


def accounts():
    return [s for s in SLOTS if connected(s)]


def enabled():
    return bool(accounts())


def public_status():
    """Status uten hemmeligheter, til visning i appen."""
    data = _load()
    return {**{k: v for k, v in status.items() if k != "accounts"}, "enabled": enabled(),
            "accounts": [{"slot": s, "connected": bool((data.get(str(s)) or {}).get("refresh_token")),
                          "connected_at": (data.get(str(s)) or {}).get("connected_at"),
                          **status["accounts"].get(s, {})} for s in SLOTS]}


# --- HTTP ---------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Følger aldri viderekoblinger – tilgangsbeviset skal ikke kunne havne hos en annen vert."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect())


def _http(req, timeout=40):
    try:
        with _opener.open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        raise RemaError(f"Får ikke kontakt med Rema: {e.reason}") from None


def _token_request(fields):
    """Det eneste POST-kallet klienten gjør: bytte kode mot bevis, eller fornye beviset."""
    body = urllib.parse.urlencode({"client_id": CLIENT_ID, **fields}).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    code, raw = _http(req)
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    if code != 200 or not data.get("access_token"):
        detail = data.get("error_description") or data.get("error") or f"feil {code}"
        err = RemaError(f"Rema godtok ikke innloggingen ({detail})")
        err.rejected = code in (400, 401, 403)
        raise err
    return data


def _store_tokens(slot, data, old=None):
    old = old or {}
    acc = {
        "access_token": data["access_token"],
        # Rema bytter som regel ut fornyelsesbeviset hver gang – behold det gamle hvis ikke
        "refresh_token": data.get("refresh_token") or old.get("refresh_token"),
        "expires_at": int(time.time()) + int(data.get("expires_in") or 3600),
        "device_id": old.get("device_id") or str(uuid.uuid4()).upper(),
        "connected_at": old.get("connected_at") or logic.now().isoformat(timespec="minutes"),
    }
    _set_account(slot, acc)
    return acc


def _refresh(slot, acc):
    try:
        data = _token_request({"grant_type": "refresh_token", "refresh_token": acc["refresh_token"]})
    except RemaError as e:
        if not e.rejected:
            raise  # forbigående feil – innloggingen kan fortsatt være god
        raise NotLoggedIn() from None
    return _store_tokens(slot, data, acc)


def _get(slot, path):
    if not (path == HEADS_PATH or (path.startswith(ROWS_PATH) and path[len(ROWS_PATH):].isdigit())):
        raise RemaError(f"Sperret: Matplan har ikke lov til å hente {path} hos Rema")
    acc = _account(slot)
    if not acc or not acc.get("refresh_token"):
        raise NotLoggedIn()
    if acc.get("expires_at", 0) - 300 < time.time():
        acc = _refresh(slot, acc)
    for attempt in (1, 2):
        req = urllib.request.Request(API_BASE + path, method="GET", headers={
            "Authorization": "Bearer " + acc["access_token"],
            "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
            "X-App": "Bella",
            "X-Platform": "android",
            "X-App-Version": APP_VERSION,
            "X-Device-ID": acc.get("device_id") or "",
            "X-Correlation-ID": str(uuid.uuid4()),
            "Accept": "application/json",
            "Accept-Language": "no",
        })
        code, raw = _http(req)
        if code == 401 and attempt == 1:
            acc = _refresh(slot, acc)
            continue
        if code in (401, 403):
            raise RemaError(
                f"Rema avviste forespørselen (feil {code}). Innloggingen gikk bra, men Rema slipper ikke "
                "Matplan inn til kvitteringene. Prøv å koble til på nytt; hjelper ikke det, har Rema trolig "
                "strammet inn tilgangen."
            )
        if code != 200:
            raise RemaError(f"Rema svarte med feil {code}")
        try:
            return json.loads(raw)
        except ValueError:
            raise RemaError("Rema svarte med noe Matplan ikke forstår") from None
    raise NotLoggedIn()


# --- Innlogging (gjøres av deg i nettleseren) ------------------------------------

def start_login(slot):
    """Lager adressen du åpner for å logge inn hos Rema. Koden Rema gir etterpå limes inn i appen."""
    slot = _check_slot(slot)
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    _pending[slot] = {"verifier": verifier, "state": state, "at": time.time()}
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": CLIENT_ID, "code_challenge_method": "S256",
        "code_challenge": challenge, "state": state, "scope": "all", "redirect_uri": REDIRECT_URI,
    })


def complete_login(slot, pasted):
    """Tar imot adressen (eller bare koden) du havnet på etter innlogging, og henter tilgangsbevis."""
    slot = _check_slot(slot)
    pend = _pending.get(slot)
    if not pend or time.time() - pend["at"] > LOGIN_MINUTES * 60:
        _pending.pop(slot, None)
        raise RemaError("Innloggingen ble startet for lenge siden. Trykk «Koble til» og prøv igjen.")
    pasted = (pasted or "").strip()
    code, state = pasted, None
    if "code=" in pasted:
        query = urllib.parse.urlparse(pasted).query or pasted.split("?", 1)[-1]
        params = urllib.parse.parse_qs(query)
        code = (params.get("code") or [""])[0]
        state = (params.get("state") or [None])[0]
    if not code or " " in code or len(code) > 4000:
        raise RemaError("Fant ingen kode. Kopier hele adressen fra adressefeltet etter innlogging (den inneholder «code=»).")
    if state is not None and state != pend["state"]:
        raise RemaError("Adressen hører til en annen innlogging enn den som sist ble startet. Trykk «Koble til» og prøv igjen.")
    data = _token_request({"grant_type": "authorization_code", "code": code,
                           "code_verifier": pend["verifier"], "redirect_uri": REDIRECT_URI})
    if not data.get("refresh_token"):
        raise RemaError("Rema ga ikke noe fornyelsesbevis, så innloggingen ville bare vart en kort stund.")
    _pending.pop(slot, None)
    _store_tokens(slot, data)
    status["accounts"].pop(slot, None)
    return True


def disconnect(slot):
    slot = _check_slot(slot)
    _pending.pop(slot, None)
    _set_account(slot, None)
    status["accounts"].pop(slot, None)


def _check_slot(slot):
    try:
        slot = int(slot)
    except (TypeError, ValueError):
        raise RemaError("Ukjent konto") from None
    if slot not in SLOTS:
        raise RemaError("Ukjent konto")
    return slot


# --- Tolking ------------------------------------------------------------------

def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _local_date(ms):
    n = _num(ms)
    if not n:
        return None
    if n > 1e11:  # millisekunder
        n /= 1000
    return datetime.fromtimestamp(n, timezone.utc).astimezone(logic.tz()).date().isoformat()


def to_receipt(head, rows):
    """Gjør én Rema-kvittering om til formatet logic.save_receipt forventer. None = ingen varer."""
    if isinstance(rows, dict):
        rows = rows.get("rows")
    lines = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        name = str(r.get("prodtxt1") or r.get("productDescription") or "").strip()
        amount = _num(r.get("amount"))
        if not name or amount is None:
            continue
        weighed = str(r.get("unit") or "").lower() in ("kg", "kgm")
        qty = _num(r.get("volume")) if weighed else (_num(r.get("pieces")) or _num(r.get("volume")))
        lines.append({"navn": name, "antall": qty if qty and qty > 0 else 1, "enhet": "kg" if weighed else "stk",
                      "belop": amount, "rabatt": _num(r.get("discount")) or 0})
    if not lines:
        return None
    total = _num(head.get("amount"))
    gross = sum(l["belop"] for l in lines)
    net = gross - sum(l["rabatt"] for l in lines)
    # Linjebeløpet skal være etter rabatt, men det er ikke bekreftet. Den varianten som
    # summerer seg nærmest kvitteringens totalbeløp, er den riktige.
    subtract = total is not None and abs(net - total) + 0.005 < abs(gross - total)
    return {
        "butikk": "Rema 1000",
        "dato": _local_date(head.get("purchaseDate")),
        "total": total if total is not None else round(gross, 2),
        "ref": f"rema:{head.get('id')}",
        "varer": [{"navn": l["navn"], "antall": l["antall"], "enhet": l["enhet"],
                   "pris": round(l["belop"] - l["rabatt"], 2) if subtract else l["belop"]} for l in lines],
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
        return "Rema er ikke koblet til (Mer → Innstillinger → Butikk-kontoer)"
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
            log.warning("Rema-henting feilet (konto %s): %s", slot, e)
            status["accounts"][slot] = {"last_result": None, "error": str(e)}
            errors.append(label + str(e))
    if saved_total:
        logic.normalize_pending(progress)
    status.update(last_result="; ".join(results) or None, error="; ".join(errors) or None)
    if errors and not results:
        raise RemaError("; ".join(errors))
    return "; ".join(results + ["Feil – " + e for e in errors])


def _poll_account(slot, progress, label=""):
    """Henter nye kvitteringer for én konto. Returnerer (antall lagret, tekst)."""
    if progress:
        progress(f"{label}Henter kjøpslista fra Rema")
    heads = _get(slot, HEADS_PATH)
    txs = heads.get("transactions") if isinstance(heads, dict) else heads
    if not isinstance(txs, list):
        raise RemaError("Rema har endret formatet – Matplan klarer ikke å lese kjøpslista lenger")
    since = (logic.today() - timedelta(days=SINCE_DAYS)).isoformat()
    seen = {r["msgid"] for r in db.q("SELECT msgid FROM mail_seen WHERE msgid LIKE 'rema:%'")}
    seen |= {r["source_ref"] for r in db.q("SELECT source_ref FROM receipts WHERE source_ref LIKE 'rema:%'")}
    todo = []
    for tx in txs:
        if not isinstance(tx, dict) or not str(tx.get("id") or "").isdigit():
            continue
        d = _local_date(tx.get("purchaseDate"))
        if f"rema:{tx['id']}" in seen or (d and d < since):
            continue
        todo.append(tx)
    todo.sort(key=lambda t: t.get("purchaseDate") or 0, reverse=True)
    todo = todo[:MAX_PER_RUN]
    by = "Rema" if slot == 1 else f"Rema (konto {slot})"
    saved = skipped = failed = 0
    for idx, tx in enumerate(todo):
        ref = f"rema:{tx['id']}"
        if progress:
            progress(f"{label}Leser Rema-kvittering {idx + 1} av {len(todo)}")
        if idx:
            time.sleep(PAUSE_SECONDS)
        try:
            data = to_receipt(tx, _get(slot, ROWS_PATH + str(tx["id"])))
            rid, res = None, "Ingen varelinjer"
            if data is not None:
                rid, res = logic.save_receipt(data, "rema", ref, by, logic.today())
        except NotLoggedIn:
            raise
        except Exception as e:
            if idx == 0:
                raise  # feiler den første, feiler trolig alle – vis feilen i stedet for å prøve 150 ganger
            log.warning("Rema-kvittering %s feilet: %s", ref, e)
            failed += 1
            continue
        if rid:
            saved += 1
        else:
            skipped += 1
        db.ex("INSERT OR REPLACE INTO mail_seen(msgid, result) VALUES(?,?)", (ref, res))
        log.info("Rema %s: %s", ref, res)
    result = "1 ny kvittering fra Rema" if saved == 1 else f"{saved} nye kvitteringer fra Rema"
    if skipped:
        result += f", {skipped} hoppet over (fantes fra før eller uten varer)"
    if failed:
        result += f", {failed} feilet (prøves igjen)"
    return saved, result


def start_scheduler():
    def loop():
        time.sleep(60)
        while True:
            if enabled():
                try:
                    poll()
                except Exception:
                    pass
            time.sleep(INTERVAL_HOURS * 3600)

    threading.Thread(target=loop, daemon=True, name="rema-poller").start()
