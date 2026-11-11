"""Kobling mot Pengeboka (husholdningsøkonomi-appen) – kodebeskyttet og smal.

Pengeboka henter kvitteringene herfra, kobler dem mot dagligvarekjøpene i banken,
og sender tilbake hvilke kvitteringer som er funnet i banken. Matplan får aldri
tilgang til bankdataene – bare svaret: «betalt fra Felleskonto 11. nov, 249 kr».

Det som er åpent (alt krever koblingskoden som Bearer-token):
  GET /kobling/kvitteringer?fra=YYYY-MM-DD   oversikt: id, butikk, dato, sum
  GET /kobling/kvitteringer/<id>             varelinjene i én kvittering
  PUT /kobling/bank                          bankstatus for kvitteringene, og dagligvarekjøp
                                             i banken som mangler kvittering
Ingenting annet i Matplan kan nås med koden.

Kjøp uten kvittering lagres som egne rader i receipts med source='bank' (uten varelinjer).
Da kommer de med i snitt, butikker og budsjett, og forsvinner av seg selv når kvitteringen
kommer inn og Pengeboka finner den.
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


def _kjop_uten_kvittering(liste) -> list[tuple] | None:
    """[(source_ref, dato, butikk, beløp, konto)] fra Pengeboka, eller None hvis lista ikke er med."""
    if not isinstance(liste, list):
        return None
    ut, sett = [], set()
    for k in liste[:MAKS_KOBLINGER]:
        if not isinstance(k, dict):
            continue
        ref = str(k.get("ref") or "")[:40]
        dato = str(k.get("dato") or "")[:10]
        belop = k.get("belop")
        if not ref or ref in sett or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dato) or not isinstance(belop, (int, float)) or belop <= 0:
            continue
        sett.add(ref)
        ut.append((f"bank:{ref}", dato, str(k.get("butikk") or "Ukjent butikk")[:60], round(float(belop), 2),
                   str(k.get("konto") or "")[:60] or None))
    return ut


def handle(method: str, path: str, query: dict, body) -> tuple[int, dict]:
    if method == "GET" and path == "/kobling/kvitteringer":
        fra = query.get("fra") or "2000-01-01"
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fra):
            return 400, {"error": "Ugyldig dato"}
        rader = db.q(
            "SELECT r.id, r.store, r.purchased_on, r.total, r.source, COUNT(i.id) AS varer "
            "FROM receipts r LEFT JOIN items i ON i.receipt_id = r.id "
            "WHERE r.source NOT IN ('manual', 'bank') AND r.purchased_on >= ? GROUP BY r.id ORDER BY r.purchased_on",
            (fra,))
        return 200, {"kvitteringer": rader}
    m = re.fullmatch(r"/kobling/kvitteringer/(\d+)", path)
    if method == "GET" and m:
        r = db.one("SELECT id, store, purchased_on, total, source FROM receipts WHERE id = ? AND source NOT IN ('manual', 'bank')",
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
        kjop = _kjop_uten_kvittering(body.get("uten_kvittering"))
        with db.tx():
            db.ex("DELETE FROM bank_links")
            for r in rader:
                db.ex("INSERT OR REPLACE INTO bank_links(receipt_id, status, bank_date, account, amount) "
                      "SELECT ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM receipts WHERE id = ? AND source != 'bank')", (*r, r[0]))
            if kjop is not None:   # eldre Pengeboka sender ikke lista – da røres ingenting
                refs = [k[0] for k in kjop]
                gamle = {r["source_ref"]: r["id"] for r in db.q("SELECT id, source_ref FROM receipts WHERE source = 'bank'")}
                for ref, dato, butikk, belop, konto in kjop:
                    if ref in gamle:
                        db.ex("UPDATE receipts SET store = ?, purchased_on = ?, total = ?, uploaded_by = ?, note = ? WHERE id = ?",
                              (butikk, dato, belop, konto, "Fra banken (Pengeboka) – mangler kvittering", gamle[ref]))
                    else:
                        db.ex("INSERT INTO receipts(store, purchased_on, total, source, source_ref, uploaded_by, note) "
                              "VALUES(?,?,?,'bank',?,?,?)",
                              (butikk, dato, belop, ref, konto, "Fra banken (Pengeboka) – mangler kvittering"))
                for ref, rid in gamle.items():
                    if ref not in set(refs):
                        db.ex("DELETE FROM receipts WHERE id = ?", (rid,))
        db.set_setting("bank_link_info", {
            "synket": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dekket_til": dekket if re.fullmatch(r"\d{4}-\d{2}-\d{2}", dekket) else None,
            "kilde": str(body.get("kilde") or "Pengeboka")[:40]})
        return 200, {"ok": True, "lagret": len(rader), "uten_kvittering": len(kjop) if kjop is not None else None}
    return 404, {"error": "Ukjent adresse"}
