"""Henter kvitteringer fra en IMAP-mappe (f.eks. Gmail-etiketten «Matplan»)."""
import email
import email.utils
import hashlib
import imaplib
import logging
import re
import threading
from datetime import timedelta
from email import policy

import db
import logic
import parse
from config import load_options

log = logging.getLogger("matplan.mail")
_lock = threading.Lock()
status = {"last_run": None, "last_result": None, "error": None}

IMAP_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def enabled():
    o = load_options()
    return bool(o.get("imap_enabled") and o.get("imap_user") and o.get("imap_password"))


def _body_text(msg):
    plain, htm = None, None
    for part in msg.walk():
        if part.is_multipart() or part.get_filename():
            continue
        ct = part.get_content_type()
        try:
            content = part.get_content()
        except Exception:
            continue
        if ct == "text/plain" and plain is None:
            plain = content
        elif ct == "text/html" and htm is None:
            htm = content
    if plain and plain.strip():
        return plain
    return parse.html_to_text(htm) if htm else ""


def _attachments(msg):
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ct = part.get_content_type()
        name = part.get_filename() or ""
        if ct.startswith("image/") or ct == "application/pdf" or name.lower().endswith(".pdf"):
            data = part.get_payload(decode=True) or b""
            # små bilder er logoer/ikoner i nyhetsbrev og kvitteringsmaler
            if ct.startswith("image/") and len(data) < 15000:
                continue
            out.append((data, ct, name))
    return out


def process_message(msg, msgid):
    subject = str(msg.get("Subject", ""))
    sender = str(msg.get("From", ""))
    by = email.utils.parseaddr(sender)[0] or email.utils.parseaddr(sender)[1]
    fallback = None
    try:
        fallback = email.utils.parsedate_to_datetime(msg.get("Date")).astimezone(logic.tz()).date()
    except Exception:
        pass
    text = _body_text(msg)

    if "bestillingsbekreftelse" in subject.lower():
        return "Hoppet over (ordrebekreftelse, ikke kvittering)"

    oda = parse.parse_oda(text)
    if oda:
        _, m = logic.save_receipt(oda, "email", f"mail:{msgid}", by, fallback)
        return m

    results = []
    for data, ct, name in _attachments(msg):
        ref = "sha:" + hashlib.sha256(data).hexdigest()
        # Feil (f.eks. AI utilgjengelig) bobler opp, så e-posten prøves igjen neste runde.
        # Vedlegg som allerede er lagret hoppes over via sha-referansen.
        results.append(logic.process_file(data, ct, name, by, "email", ref, fallback))
    if results:
        return "; ".join(results)

    if parse.looks_like_receipt_text(text):
        return logic.process_text(text, by, "email", f"mail:{msgid}", fallback)
    return "Fant ingen kvittering i e-posten"


def poll(progress=None):
    if not _lock.acquire(blocking=False):
        return "Henting pågår allerede"
    try:
        return _poll(progress)
    finally:
        _lock.release()


def _poll(progress):
    o = load_options()
    if not enabled():
        return "E-post er ikke satt opp (se app-innstillingene i Home Assistant)"
    status["last_run"] = logic.now().isoformat(timespec="minutes")
    try:
        M = imaplib.IMAP4_SSL(o.get("imap_host") or "imap.gmail.com", 993, timeout=60)
        user = "".join(str(o["imap_user"]).split())
        # App-passord vises i grupper med mellomrom – fjern alt av mellomrom/usynlige tegn
        pw = "".join(str(o["imap_password"]).split()).replace("\u200b", "")
        try:
            M.login(user, pw)
        except imaplib.IMAP4.error as e:
            if b"AUTHENTICATIONFAILED" in str(e).encode() or "AUTHENTICATIONFAILED" in str(e):
                raise RuntimeError(
                    "Gmail avviste innloggingen. Sjekk at e-postadressen er riktig og at du bruker "
                    "et app-passord (16 bokstaver) fra myaccount.google.com/apppasswords – ikke det "
                    "vanlige passordet."
                ) from None
            raise
        folder = o.get("imap_folder") or "Matplan"
        typ, _ = M.select('"%s"' % folder.replace('"', ""), readonly=True)
        if typ != "OK":
            raise RuntimeError(f"Fant ikke mappen/etiketten «{folder}»")
        since = logic.today() - timedelta(days=int(o.get("imap_since_days") or 365))
        crit = f"{since.day:02d}-{IMAP_MONTHS[since.month - 1]}-{since.year}"
        typ, data = M.search(None, "SINCE", crit)
        nums = data[0].split() if data and data[0] else []
        new = []
        # Hent Message-ID for alle i én runde
        for i in range(0, len(nums), 200):
            chunk = b",".join(nums[i:i + 200]).decode()
            typ, resp = M.fetch(chunk, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
            for part in resp:
                if not isinstance(part, tuple):
                    continue
                num = part[0].split()[0]
                m = re.search(rb"Message-ID:\s*(<[^>]+>|\S+)", part[1], re.I)
                mid = (m.group(1) if m else b"num-" + num).decode("utf-8", "replace").strip()
                if not db.one("SELECT 1 FROM mail_seen WHERE msgid=?", (mid,)):
                    new.append((num, mid))
        handled, saved, failed = 0, 0, 0
        for idx, (num, mid) in enumerate(new):
            if progress:
                progress(f"Leser e-post {idx + 1} av {len(new)}")
            typ, resp = M.fetch(num.decode(), "(BODY.PEEK[])")
            raw = next((p[1] for p in resp if isinstance(p, tuple)), None)
            if not raw:
                continue
            msg = email.message_from_bytes(raw, policy=policy.default)
            try:
                res = process_message(msg, mid)
            except Exception as e:
                log.warning("Feil ved e-post %s (prøves igjen senere): %s", mid, e)
                failed += 1
                continue
            db.ex("INSERT OR REPLACE INTO mail_seen(msgid, result) VALUES(?,?)", (mid, res))
            handled += 1
            if "varer" in res:
                saved += 1
            log.info("E-post %s: %s", mid, res)
        M.logout()
        if handled:
            logic.normalize_pending(progress)
        result = f"{handled} nye e-poster, {saved} kvitteringer lagret"
        if failed:
            result += f", {failed} feilet (prøves igjen)"
        status.update(last_result=result, error=None)
        return result
    except Exception as e:
        status["error"] = str(e)
        log.warning("E-posthenting feilet: %s", e)
        raise


def start_scheduler():
    def loop():
        import time
        time.sleep(20)
        while True:
            o = load_options()
            if enabled():
                try:
                    poll()
                except Exception:
                    pass
            time.sleep(max(5, int(o.get("imap_interval_minutes") or 15)) * 60)

    threading.Thread(target=loop, daemon=True, name="mail-poller").start()
