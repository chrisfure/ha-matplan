"""Kobling mot Pengeboka (husholdningsøkonomi-appen) – kodebeskyttet og smal.

Pengeboka henter kvitteringene herfra, kobler dem mot dagligvarekjøpene i banken,
og sender tilbake hvilke kvitteringer som er funnet i banken. Matplan får aldri
tilgang til bankdataene – bare svaret: «betalt fra Felleskonto 11. nov, 249 kr».

Det som er åpent (alt krever koblingskoden som Bearer-token):
  GET /kobling/kvitteringer?fra=YYYY-MM-DD   oversikt: id, butikk, dato, sum
  GET /kobling/kvitteringer/<id>             varelinjene i én kvittering
  PUT /kobling/bank                          bankstatus for kvitteringene
Ingenting annet i Matplan kan nås med koden.
"""
import hmac
import re
import secrets
from datetime import datetime, timezone

import db

MAKS_KOBLINGER = 20000


def kode() -> str:
    """Koblingskoden. Lages første gang den trengs, og kan byttes i innstillingene."""
    k = db.get_setting("link_token")
    if not k:
        k = secrets.token_urlsafe(24)
        db.set_setting("link_token", k)
    return k


def ny_kode() -> str:
    db.set_setting("link_token", "")
    return kode()


def gyldig(authorization: str | None) -> bool:
    if not authorization or not authorization.startswith("Bearer "):
        return False
    return hmac.compare_digest(authorization[7:].strip().encode(), kode().encode())


def status() -> dict:
    info = db.get_setting("bank_link_info") or {}
    n = db.one("SELECT COUNT(*) AS n, SUM(status='funnet') AS funnet FROM bank_links") or {}
    return {"kode": kode(), "synket": info.get("synket"), "dekket_til": info.get("dekket_til"),
            "kilde": info.get("kilde"), "funnet": n.get("funnet") or 0, "vurdert": n.get("n") or 0}


def handle(method: str, path: str, query: dict, body) -> tuple[int, dict]:
    if method == "GET" and path == "/kobling/kvitteringer":
        fra = query.get("fra") or "2000-01-01"
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fra):
            return 400, {"error": "Ugyldig dato"}
        rader = db.q(
            "SELECT r.id, r.store, r.purchased_on, r.total, r.source, COUNT(i.id) AS varer "
            "FROM receipts r LEFT JOIN items i ON i.receipt_id = r.id "
            "WHERE r.source != 'manual' AND r.purchased_on >= ? GROUP BY r.id ORDER BY r.purchased_on",
            (fra,))
        return 200, {"kvitteringer": rader}
    m = re.fullmatch(r"/kobling/kvitteringer/(\d+)", path)
    if method == "GET" and m:
        r = db.one("SELECT id, store, purchased_on, total, source FROM receipts WHERE id = ? AND source != 'manual'",
                   (int(m.group(1)),))
        if not r:
            return 404, {"error": "Fant ikke kvitteringen"}
        r["varer"] = db.q("SELECT i.raw_name AS navn, p.name AS produkt, i.qty AS antall, i.unit AS enhet, i.price AS pris "
                          "FROM items i LEFT JOIN products p ON p.id = i.product_id WHERE i.receipt_id = ? ORDER BY i.id",
                          (r["id"],))
        return 200, r
    if method == "PUT" and path == "/kobling/bank":
        if not isinstance(body, dict) or not isinstance(body.get("koblinger"), list):
            return 400, {"error": "Mangler koblinger"}
        rader = []
        for k in body["koblinger"][:MAKS_KOBLINGER]:
            if not isinstance(k, dict):
                continue
            try:
                rid = int(k["kvittering"])
            except (KeyError, TypeError, ValueError):
                continue
            st = k.get("status")
            if st not in ("funnet", "mangler"):
                continue
            dato = str(k.get("dato") or "")[:10] or None
            if dato and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dato):
                dato = None
            belop = k.get("belop")
            belop = round(float(belop), 2) if isinstance(belop, (int, float)) else None
            rader.append((rid, st, dato, str(k.get("konto") or "")[:60] or None, belop))
        dekket = str(body.get("dekket_til") or "")[:10]
        with db.tx():
            db.ex("DELETE FROM bank_links")
            for r in rader:
                db.ex("INSERT OR REPLACE INTO bank_links(receipt_id, status, bank_date, account, amount) "
                      "SELECT ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM receipts WHERE id = ?)", (*r, r[0]))
        db.set_setting("bank_link_info", {
            "synket": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dekket_til": dekket if re.fullmatch(r"\d{4}-\d{2}-\d{2}", dekket) else None,
            "kilde": str(body.get("kilde") or "Pengeboka")[:40]})
        return 200, {"ok": True, "lagret": len(rader)}
    return 404, {"error": "Ukjent adresse"}
