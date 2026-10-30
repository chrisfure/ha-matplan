"""Henter e-post fra barnehage, skole og aktiviteter via IMAP (Gmail med app-passord) og lager forslag."""
import email
import email.utils
import imaplib
import logging
import re
import threading
import time
from datetime import timedelta
from email import policy

import db
import extract
import logic
import util
from config import load_options

log = logging.getLogger("huskelappen.mail")
_lock = threading.Lock()
status = {"last_run": None, "last_result": None, "error": None}

IMAP_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def sources(s=None):
    """Kildene appen leser fra. Uten egne kilder brukes imap_sender fra konfigurasjonen for alle barna."""
    s = s or db.all_settings()
    names = [c["name"] for c in s.get("children") or [] if c.get("name")]
    out = []
    for src in s.get("sources") or []:
        sender = (src.get("sender") or "").strip().replace('"', "")
        if sender:
            kids = [k for k in src.get("children") or [] if k in names] or names
            out.append({"name": (src.get("name") or sender).strip(), "sender": sender, "children": kids})
    if not out:
        sender = (load_options().get("imap_sender") or "").strip().replace('"', "")
        if sender:
            out.append({"name": s.get("kindergarten") or "Barnehagen", "sender": sender, "children": names})
    return out


def enabled():
    o = load_options()
    # Minst én avsender er påkrevd, ellers ville appen lest all e-post i innboksen.
    return bool(o.get("imap_enabled") and o.get("imap_user") and o.get("imap_password") and sources())


def has_children():
    return bool([c for c in (db.get_setting("children") or []) if (c.get("name") or "").strip()])


def source_for(sender_header, srcs=None):
    addr = (email.utils.parseaddr(sender_header)[1] or sender_header).lower()
    for src in srcs if srcs is not None else sources():
        if src["sender"].lower() in addr:
            return src
    return None


def body_text(msg):
    plain, htm = None, None
    for part in msg.walk():
        if part.is_multipart() or part.get_filename():
            continue
        try:
            content = part.get_content()
        except Exception:
            continue
        ct = part.get_content_type()
        if ct == "text/plain" and plain is None:
            plain = content
        elif ct == "text/html" and htm is None:
            htm = content
    if plain and plain.strip():
        return plain
    return util.html_to_text(htm) if htm else ""


def attachments(msg):
    """PDF-er og bilder (bilder under 15 kB er logoer/ikoner og hoppes over)."""
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = part.get_filename() or ""
        ct = part.get_content_type()
        if ct == "application/pdf" or name.lower().endswith(".pdf"):
            out.append((name or "vedlegg.pdf", part.get_payload(decode=True) or b""))
        elif ct in ("image/jpeg", "image/png"):
            data = part.get_payload(decode=True) or b""
            if len(data) >= 15000:
                out.append((name or "bilde.jpg", data))
    return out


def ignored(subject):
    for pat in db.get_setting("ignore_subjects") or []:
        try:
            if re.search(pat, subject, re.I):
                return pat
        except re.error:
            if pat.lower() in subject.lower():
                return pat
    return None


def process(subject, sender, received, body, files, msgid, source=None):
    """Felles for e-post og opplasting. Returnerer (melding, nye punkter)."""
    pat = ignored(subject)
    if pat:
        return f"Hoppet over (emnet passer «{pat}»)", []
    s = db.all_settings()
    src_name = None
    if source:
        s["children"] = [c for c in s.get("children") or [] if c.get("name") in source["children"]]
        src_name = source["name"]
    points = extract.read_mail(subject, sender, received, body, files, s, source_name=src_name)
    new = extract.save(extract.clean(points, s), msgid, subject, src_name)
    auto = {"rejected": 0, "approved": 0}
    pending = []
    for it in new:
        st = logic.learn(it)
        if st == "pending":
            pending.append(it)
        else:
            auto[st] += 1
    if pending:
        logic.after_new_items(pending)
    if new:
        logic.push_sensors()
    res = f"{len(points)} funnet, {len(new)} nye forslag"
    if auto["rejected"] or auto["approved"]:
        res += f" ({auto['rejected']} avvist og {auto['approved']} godkjent automatisk)"
    return res, new


def handle_message(msg, msgid, source=None):
    subject = str(msg.get("Subject", ""))
    sender = str(msg.get("From", ""))
    try:
        received = email.utils.parsedate_to_datetime(msg.get("Date")).astimezone(util.tz()).date()
    except Exception:
        received = util.today()
    res, _ = process(subject, sender, received, body_text(msg), attachments(msg), msgid,
                     source or source_for(sender))
    db.ex("INSERT OR REPLACE INTO mails(msgid, subject, sender, received, result) VALUES(?,?,?,?,?)",
          (msgid, subject, sender, received.isoformat(), res))
    log.info("«%s»: %s", subject, res)
    return res


def poll():
    if not _lock.acquire(blocking=False):
        return "Henting pågår allerede"
    try:
        return _poll()
    finally:
        _lock.release()


def _poll():
    o = load_options()
    if not enabled():
        return "E-post er ikke satt opp (e-post, app-passord og avsender i app-konfigurasjonen)"
    if not has_children():
        return "Legg inn barna under Innstillinger før e-post leses"
    status["last_run"] = util.now().isoformat(timespec="minutes")
    try:
        M = imaplib.IMAP4_SSL(o.get("imap_host") or "imap.gmail.com", 993, timeout=60)
        user = "".join(str(o["imap_user"]).split())
        pw = "".join(str(o["imap_password"]).split()).replace("​", "")
        try:
            M.login(user, pw)
        except imaplib.IMAP4.error as e:
            if "AUTHENTICATIONFAILED" in str(e):
                raise RuntimeError("Gmail avviste innloggingen. Bruk et app-passord (16 bokstaver) "
                                   "fra myaccount.google.com/apppasswords.") from None
            raise
        folder = o.get("imap_folder") or "INBOX"
        typ, _ = M.select('"%s"' % folder.replace('"', ""), readonly=True)
        if typ != "OK":
            raise RuntimeError(f"Fant ikke mappen/etiketten «{folder}»")
        since = util.today() - timedelta(days=int(o.get("imap_since_days") or 14))
        since_crit = ["SINCE", f"{since.day:02d}-{IMAP_MONTHS[since.month - 1]}-{since.year}"]
        todo = []
        for src in sources():
            typ, data = M.search(None, "FROM", f'"{src["sender"]}"', *since_crit)
            todo += [(num, src) for num in (data[0].split() if data and data[0] else [])]
        handled, new_items, failed = 0, 0, 0
        for num, src in todo:
            typ, resp = M.fetch(num.decode(), "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
            hdr = next((p[1] for p in resp if isinstance(p, tuple)), b"")
            m = re.search(rb"Message-ID:\s*(<[^>]+>|\S+)", hdr, re.I)
            mid = (m.group(1) if m else b"num-" + num).decode("utf-8", "replace").strip()
            if db.one("SELECT 1 FROM mails WHERE msgid=?", (mid,)):
                continue
            typ, resp = M.fetch(num.decode(), "(BODY.PEEK[])")
            raw = next((p[1] for p in resp if isinstance(p, tuple)), None)
            if not raw:
                continue
            try:
                res = handle_message(email.message_from_bytes(raw, policy=policy.default), mid, src)
                handled += 1
                m2 = re.search(r"(\d+) nye forslag", res)
                new_items += int(m2.group(1)) if m2 else 0
            except Exception as e:  # prøves igjen neste runde
                log.warning("Feil ved e-post %s (prøves igjen): %s", mid, e)
                failed += 1
        M.logout()
        result = f"{handled} nye e-poster, {new_items} nye forslag"
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
        time.sleep(15)
        while True:
            o = load_options()
            if enabled():
                try:
                    poll()
                except Exception:
                    pass
            time.sleep(max(10, int(o.get("imap_interval_minutes") or 30)) * 60)

    threading.Thread(target=loop, daemon=True, name="mail-poller").start()
