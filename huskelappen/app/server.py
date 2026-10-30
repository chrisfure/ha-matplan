"""HTTP-server for Huskelappen (kjører bak Home Assistant ingress)."""
import base64
import json
import logging
import mimetypes
import os
import re
import threading
import traceback
import uuid
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import db
import ha
import logic
import mailer
import util
from config import DEV, INGRESS_IP, PORT

log = logging.getLogger("huskelappen.http")
STATIC = os.path.join(os.path.dirname(__file__), "static")
JOBS = {}
ROUTES = []


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


def start_job(kind, fn):
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "kind": kind, "status": "running", "result": None, "error": None}
    JOBS[jid] = job

    def run():
        try:
            job["result"] = fn()
            job["status"] = "done"
        except Exception as e:
            log.error("Jobb %s feilet: %s\n%s", kind, e, traceback.format_exc())
            job["error"] = str(e)
            job["status"] = "error"
        for k in list(JOBS)[:-30]:
            JOBS.pop(k, None)

    threading.Thread(target=run, daemon=True).start()
    return job


DASHBOARD_CARD = """type: conditional
conditions:
  - condition: state
    entity: binary_sensor.huskelappen_vis
    state: "on"
card:
  type: markdown
  content: "{{ state_attr('sensor.huskelappen_barnehagen', 'tekst') }}"
"""


def guess_calendars():
    """Første gang: koble Ola → calendar.ola osv. ut fra kalendernavnet."""
    s = db.all_settings()
    kids = s.get("children") or []
    if all(c.get("calendar") for c in kids):
        return
    try:
        cals = [x for x in ha.states() if x["entity_id"].startswith("calendar.")]
    except Exception:
        return
    changed = False
    for c in kids:
        if c.get("calendar"):
            continue
        for x in cals:
            fname = (x["attributes"].get("friendly_name") or "").strip().lower()
            if fname == c["name"].lower() or x["entity_id"] == "calendar." + c["name"].lower():
                c["calendar"] = x["entity_id"]
                changed = True
                break
    if changed:
        db.set_setting("children", kids)


@route("GET", "/api/state")
def api_state(req, body):
    guess_calendars()
    today = util.today()
    d, label = logic.screen_window()
    preview_day = d or today + timedelta(days=1)
    lines = logic.day_lines(preview_day)
    return {
        "user": req.user,
        "today": today.isoformat(),
        "settings": db.all_settings(),
        "mail": {"enabled": mailer.enabled(), **mailer.status},
        "items": logic.items(from_day=today.isoformat()),
        "pending": logic.pending_count(),
        "next_review": logic.next_review().isoformat(timespec="minutes"),
        "screen": {"showing": bool(d and lines), "label": label or "I morgen", "day": preview_day.isoformat(),
                   "day_text": util.nice_date(preview_day), "lines": lines},
        "mails": db.q("SELECT subject, received, result FROM mails ORDER BY received DESC, at DESC LIMIT 15"),
        "dashboard_card": DASHBOARD_CARD,
    }


@route("GET", "/api/entities")
def api_entities(req, body):
    try:
        st = ha.states()
    except Exception as e:
        raise ApiError(str(e), 502)

    def pick(prefix):
        return [{"entity_id": x["entity_id"], "name": x["attributes"].get("friendly_name", x["entity_id"])}
                for x in st if x["entity_id"].startswith(prefix)]
    notifies = []
    try:
        for dom in ha._req("GET", "/services") or []:
            if dom.get("domain") == "notify":
                notifies += ["notify." + svc for svc in (dom.get("services") or {}) if svc.startswith("mobile_app_")]
    except Exception:
        pass
    return {"calendar": pick("calendar."), "ai_task": pick("ai_task."), "notify": sorted(notifies)}


@route("POST", "/api/settings")
def api_settings(req, body):
    for k, v in (body or {}).items():
        if k in db.DEFAULT_SETTINGS:
            db.set_setting(k, v)
    logic.push_sensors()
    return db.all_settings()


@route("POST", r"/api/items/(\d+)")
def api_item_update(req, body, iid):
    try:
        return logic.update(int(iid), body or {})
    except (KeyError, ValueError) as e:
        raise ApiError(str(e))


@route("POST", r"/api/items/(\d+)/approve")
def api_approve(req, body, iid):
    try:
        return logic.approve(int(iid))
    except (KeyError, RuntimeError) as e:
        raise ApiError(str(e))


@route("POST", r"/api/items/(\d+)/reject")
def api_reject(req, body, iid):
    logic.reject(int(iid))
    return {"ok": True}


@route("POST", r"/api/items/(\d+)/restore")
def api_restore(req, body, iid):
    logic.restore(int(iid))
    return {"ok": True}


@route("POST", "/api/approve_all")
def api_approve_all(req, body):
    ok, errors = 0, []
    for it in logic.items("pending", util.today().isoformat()):
        try:
            logic.approve(it["id"])
            ok += 1
        except Exception as e:
            errors.append(f"{it['title']}: {e}")
    return {"approved": ok, "errors": errors}


@route("POST", "/api/items")
def api_add(req, body):
    try:
        date.fromisoformat((body or {}).get("day", ""))
    except ValueError:
        raise ApiError("Velg en dato")
    return logic.add_manual(body)


@route("POST", "/api/poll")
def api_poll(req, body):
    return start_job("poll", mailer.poll)


@route("POST", "/api/upload")
def api_upload(req, body):
    """Last opp en ukeplan for hånd (PDF) – nyttig for å teste uten e-post."""
    b = body or {}
    try:
        data = base64.b64decode(b.get("data") or "")
    except Exception:
        raise ApiError("Kunne ikke lese filen")
    if not data:
        raise ApiError("Tom fil")
    if not mailer.has_children():
        raise ApiError("Legg inn barna under Innstillinger først")
    name = b.get("filename") or "ukeplan.pdf"
    msgid = "upload:" + uuid.uuid4().hex[:8]

    def run():
        res, new = mailer.process(name, "Lastet opp", util.today(), "", [(name, data)], msgid)
        return res
    return start_job("upload", run)


@route("GET", r"/api/jobs/(\w+)")
def api_job(req, body, jid):
    job = JOBS.get(jid)
    if not job:
        raise ApiError("Ukjent jobb", 404)
    return job


@route("POST", "/api/test_notify")
def api_test_notify(req, body):
    if not logic.notify("Huskelappen", "Testvarsel – slik ser varslene ut."):
        raise ApiError("Velg hvilken telefon som skal varsles først")
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    server_version = "Huskelappen"

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _allowed(self):
        return DEV or self.client_address[0] in (INGRESS_IP, "127.0.0.1")

    @property
    def user(self):
        return {"name": self.headers.get("X-Remote-User-Display-Name") or self.headers.get("X-Remote-User-Name")}

    def _send(self, code, payload, ctype="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC, rel))
        if not full.startswith(os.path.realpath(STATIC)) or not os.path.isfile(full):
            full = os.path.join(STATIC, "index.html")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def _handle(self, method):
        if not self._allowed():
            return self._send(403, {"error": "Kun tilgjengelig via Home Assistant"})
        path = unquote(urlparse(self.path).path)
        i = path.find("/api/")
        if i < 0:
            return self._static(path) if method == "GET" else self._send(405, {"error": "Ikke tillatt"})
        path = path[i:]
        body = None
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            if n > 40 * 1024 * 1024:
                return self._send(413, {"error": "For stor fil"})
            try:
                body = json.loads(self.rfile.read(n)) if n else {}
            except json.JSONDecodeError:
                return self._send(400, {"error": "Ugyldig JSON"})
        for m, rx, fn in ROUTES:
            mt = rx.match(path) if m == method else None
            if mt:
                try:
                    return self._send(200, fn(self, body, *mt.groups()))
                except ApiError as e:
                    return self._send(e.code, {"error": str(e)})
                except ha.HAError as e:
                    return self._send(502, {"error": str(e)})
                except Exception as e:
                    log.error("Feil i %s %s: %s\n%s", method, path, e, traceback.format_exc())
                    return self._send(500, {"error": str(e)})
        return self._send(404, {"error": "Ukjent adresse"})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def run():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    log.info("Huskelappen lytter på port %s", PORT)
    srv.serve_forever()
