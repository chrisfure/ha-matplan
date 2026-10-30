"""Oda-klient for Matplan – KUN lese og legge til i handlekurven.

Sikkerhet: Denne klienten kan aldri bestille noe. Den har bare tre typer kall:
  1. logge inn
  2. lese (søkesider, handlekurv, tidligere ordrer)
  3. legge varer i handlekurven med POSITIVT antall

Alle POST-kall går gjennom _post(), som stopper alt som ikke står på hvitelisten
under. Det finnes ingen kode for kasse, betaling, leveringstid, fjerning eller
tømming av handlekurven. Bestillingen fullfører du selv i Oda-appen.

Bygger på beskrivelsen av Odas nettside-API i github.com/gbbirkisson/mcp-oda (ODA_API.md).
"""
import http.cookiejar
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from config import DATA_DIR, load_options

log = logging.getLogger("matplan.oda")

BASE = os.environ.get("ODA_BASE", "https://oda.com")
COOKIE_FILE = os.path.join(DATA_DIR, "oda_cookies.txt")
UA = ("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")

# Hviteliste: de ENESTE stiene klienten får POSTe til.
ALLOWED_POST = {"/api/v1/user/login/", "/api/v1/cart/items/"}
# Hviteliste for GET (prefikser)
ALLOWED_GET = ("/no/user/login/", "/no/search/products/", "/no/cart/", "/api/v1/cart/",
               "/api/v1/orders/")


class OdaError(Exception):
    pass


class OdaClient:
    def __init__(self):
        self.lock = threading.RLock()
        self.jar = http.cookiejar.LWPCookieJar(COOKIE_FILE)
        try:
            if os.path.exists(COOKIE_FILE):
                self.jar.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self._search_cache = {}

    # --- lavnivå ------------------------------------------------------------

    def _save(self):
        try:
            self.jar.save(ignore_discard=True, ignore_expires=True)
            os.chmod(COOKIE_FILE, 0o600)
        except Exception:
            pass

    def _csrf(self):
        for c in self.jar:
            if c.name == "csrftoken":
                return c.value
        return None

    def _check_path(self, path, allowed):
        p = urllib.parse.urlparse(path).path
        if isinstance(allowed, set):
            ok = p in allowed
        else:
            ok = any(p.startswith(a) for a in allowed)
        if not ok:
            raise OdaError(f"Sperret: Matplan har ikke lov til å kalle {p} hos Oda")
        return p

    def _request(self, method, path, body=None, accept_json=True, referer=None, timeout=30):
        url = BASE + path
        headers = {
            "User-Agent": UA,
            "Accept-Language": "no,nb;q=0.9,en;q=0.8",
            "Accept": "application/json" if accept_json else
                      "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Origin": BASE,
            "Referer": referer or f"{BASE}/no/",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        csrf = self._csrf()
        if csrf and accept_json:
            headers["X-CSRFToken"] = csrf
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                status = r.status
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            status = e.code
        except urllib.error.URLError as e:
            raise OdaError(f"Får ikke kontakt med Oda: {e.reason}")
        self._save()
        return status, raw

    def _get(self, path, accept_json=True):
        self._check_path(path, ALLOWED_GET)
        return self._request("GET", path, accept_json=accept_json)

    def _post(self, path, body, referer=None):
        p = self._check_path(path, ALLOWED_POST)
        if p == "/api/v1/cart/items/":
            items = (body or {}).get("items") if isinstance(body, dict) else None
            if not items or not isinstance(items, list):
                raise OdaError("Sperret: ugyldig handlekurv-forespørsel")
            for it in items:
                if set(it) - {"product_id", "quantity"}:
                    raise OdaError("Sperret: bare produkt og antall er tillatt")
                q = it.get("quantity")
                if not isinstance(q, (int, float)) or q <= 0:
                    raise OdaError("Sperret: Matplan kan bare legge til varer, ikke fjerne")
        return self._request("POST", path, body, referer=referer)

    # --- innlogging -----------------------------------------------------------

    def credentials(self):
        o = load_options()
        return (o.get("oda_email") or "").strip(), (o.get("oda_password") or "").strip()

    def configured(self):
        e, p = self.credentials()
        return bool(e and p)

    def login(self):
        email, password = self.credentials()
        if not (email and password):
            raise OdaError("Oda er ikke satt opp – legg inn e-post og passord i app-konfigurasjonen i Home Assistant")
        with self.lock:
            self._get("/no/user/login/", accept_json=False)
            status, raw = self._post("/api/v1/user/login/", {"username": email, "password": password},
                                     referer=f"{BASE}/no/user/login/")
            if status in (400, 401, 403):
                raise OdaError("Oda avviste innloggingen – sjekk e-post og passord")
            if status >= 300:
                raise OdaError(f"Innlogging hos Oda feilet ({status})")
            log.info("Logget inn hos Oda")
            return True

    def _authed_json(self, method, path, body=None, referer=None):
        """Kall som krever innlogging; logger inn på nytt én gang ved behov."""
        for attempt in (1, 2):
            if not self._csrf():
                self.login()
            if method == "GET":
                status, raw = self._get(path)
            else:
                status, raw = self._post(path, body, referer=referer)
            if status in (401, 403) and attempt == 1:
                self.login()
                continue
            if status == 425:
                raise OdaError("Oda er travel akkurat nå – prøv igjen om litt")
            if status >= 300:
                raise OdaError(f"Oda svarte {status}: {raw[:200]}")
            try:
                return json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                raise OdaError("Uventet svar fra Oda")
        raise OdaError("Fikk ikke logget inn hos Oda")

    # --- lese ----------------------------------------------------------------

    def get_cart(self):
        data = self._authed_json("GET", "/api/v1/cart/")
        items = []
        raw_items = list(data.get("items") or [])
        for g in data.get("groups") or []:
            raw_items += g.get("items") or []
        for it in raw_items:
            p = it.get("product") or {}
            items.append({"id": p.get("id"), "name": p.get("full_name") or p.get("name"),
                          "quantity": it.get("quantity") or 1})
        return {"label": data.get("label_text") or "", "count": data.get("product_quantity_count") or 0,
                "total": _num(data.get("display_price")), "items": items}

    def order_history(self, max_orders=40):
        """Produkter fra tidligere Oda-ordrer: {product_id: {name, times}} (kun lesing)."""
        path = "/api/v1/orders/"
        numbers, seen = [], set()
        while path and len(numbers) < max_orders and path not in seen:
            seen.add(path)
            page = self._authed_json("GET", path)
            for month in page.get("results") or []:
                for o in month.get("orders") or []:
                    if o.get("order_number") and len(numbers) < max_orders:
                        numbers.append(str(o["order_number"]))
            nxt = page.get("get_more_url") if page.get("has_more") else None
            if nxt:
                u = urllib.parse.urlparse(urllib.parse.urljoin(BASE + "/", nxt))
                if f"{u.scheme}://{u.netloc}" != BASE:
                    break  # aldri send innloggingen til et annet nettsted
                path = u.path + (("?" + u.query) if u.query else "")
            else:
                path = None
        products = {}
        for n in numbers:
            d = self._authed_json("GET", f"/api/v1/orders/{urllib.parse.quote(n)}/")
            counted = set()
            for g in (d.get("items") or {}).get("item_groups") or []:
                for it in g.get("items") or []:
                    pid, desc = it.get("product_id"), it.get("description")
                    if not pid or not desc:
                        continue
                    e = products.setdefault(pid, {"name": desc, "times": 0})
                    if pid not in counted:
                        e["times"] += 1
                        counted.add(pid)
            time.sleep(0.2)
        return products

    def search(self, query, limit=6):
        q = (query or "").strip()
        key = q.lower()
        hit = self._search_cache.get(key)
        if hit and time.time() - hit[0] < 6 * 3600:
            return hit[1]
        status, html = self._get("/no/search/products/?" + urllib.parse.urlencode({"q": q}), accept_json=False)
        if status == 425:
            raise OdaError("Oda er travel akkurat nå – prøv igjen om litt")
        if status >= 300:
            raise OdaError(f"Søk hos Oda feilet ({status})")
        data = find_query(extract_queries(html), "mixedSearch")
        if not data or not isinstance(data.get("items"), list):
            raise OdaError("Fant ikke søkeresultater – Oda kan ha endret nettsiden sin")
        out = []
        for it in data["items"]:
            if it.get("type") != "product":
                continue
            a = it.get("attributes") or {}
            disc = a.get("discount") or {}
            discount = None
            if disc.get("isDiscounted"):
                discount = {
                    "regular": _num(disc.get("undiscountedGrossPrice")),
                    "text": disc.get("descriptionShort") or "",
                    "max_qty": disc.get("maximumQuantity"),
                }
            out.append({
                "id": a.get("id") or it.get("id"),
                "name": a.get("fullName") or a.get("name") or "",
                "extra": a.get("nameExtra") or "",
                "price": _num(a.get("grossPrice")),
                "unit_price": _num(a.get("grossUnitPrice")),
                "unit": a.get("unitPriceQuantityAbbreviation") or "",
                "discount": discount,
                "available": (a.get("availability") or {}).get("isAvailable", True) is not False,
                "image": ((a.get("images") or [{}])[0].get("thumbnail") or {}).get("url"),
            })
            if len(out) >= limit:
                break
        self._search_cache[key] = (time.time(), out)
        return out

    # --- legge til (eneste endring Matplan gjør) ------------------------------

    def add_to_cart(self, items):
        """items: [{product_id, quantity}] med positivt antall. Bestiller ingenting."""
        clean = []
        for it in items:
            pid, qty = int(it["product_id"]), int(it.get("quantity") or 1)
            if pid > 0 and qty > 0:
                clean.append({"product_id": pid, "quantity": qty})
        if not clean:
            raise OdaError("Ingen varer å legge til")
        self._authed_json("POST", "/api/v1/cart/items/", {"items": clean}, referer=f"{BASE}/no/cart/")
        return self.get_cart()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --- parsing av Odas nettsider (Next.js) ---------------------------------------

_dec = json.JSONDecoder()


def extract_queries(html):
    """Henter React Query-data fra siden (både ny og gammel Next.js-struktur)."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            return json.loads(m.group(1))["props"]["pageProps"]["dehydratedState"]["queries"]
        except Exception:
            pass
    flight = []
    for m in re.finditer(r"self\.__next_f\.push\(\s*", html):
        try:
            arr, _ = _dec.raw_decode(html, m.end())
        except json.JSONDecodeError:
            continue
        if isinstance(arr, list) and len(arr) > 1 and arr[0] == 1 and isinstance(arr[1], str):
            flight.append(arr[1])
    payload = "".join(flight)
    queries = []
    for m in re.finditer(r'"queries"\s*:\s*\[', payload):
        try:
            arr, _ = _dec.raw_decode(payload, m.end() - 1)
        except json.JSONDecodeError:
            continue
        queries += [q for q in arr if isinstance(q, dict) and "queryKey" in q]
    return queries


def find_query(queries, name):
    for q in queries or []:
        key = q.get("queryKey")
        if not isinstance(key, list) or not key:
            continue
        first = key[0]
        if first == name or (isinstance(first, dict) and first.get("_id") == name):
            return (q.get("state") or {}).get("data")
    return None


client = OdaClient()
