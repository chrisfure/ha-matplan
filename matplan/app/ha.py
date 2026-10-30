"""Tynn klient mot Home Assistant sitt REST-API (via Supervisor-proxy)."""
import json
import urllib.error
import urllib.parse
import urllib.request

from config import HA_TOKEN, HA_URL


class HAError(Exception):
    pass


def _req(method, path, body=None, timeout=30):
    url = HA_URL.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise HAError(f"Home Assistant svarte {e.code} på {path}: {detail}")
    except urllib.error.URLError as e:
        raise HAError(f"Får ikke kontakt med Home Assistant: {e.reason}")
    except TimeoutError:
        raise HAError(f"Home Assistant brukte for lang tid på {path}")


def states():
    return _req("GET", "/states") or []


def config():
    return _req("GET", "/config") or {}


def call_service(domain, service, data, return_response=False, timeout=30):
    path = f"/services/{domain}/{service}"
    if return_response:
        path += "?return_response"
    res = _req("POST", path, data, timeout)
    if return_response:
        return (res or {}).get("service_response", {}) or {}
    return res


def calendar_events(entity_id, start, end):
    q = urllib.parse.urlencode({"start": start.isoformat(), "end": end.isoformat()})
    return _req("GET", f"/calendars/{entity_id}?{q}") or []


def ai_task(instructions, entity_id=None, attachments=None, task_name="Matplan", timeout=300):
    data = {"task_name": task_name, "instructions": instructions}
    if entity_id:
        data["entity_id"] = entity_id
    if attachments:
        data["attachments"] = attachments
    resp = call_service("ai_task", "generate_data", data, True, timeout)
    out = resp.get("data")
    if out is None:
        raise HAError("AI-oppgaven returnerte ikke noe svar")
    return out


def todo_items(entity_id):
    r = call_service(
        "todo", "get_items", {"entity_id": entity_id, "status": ["needs_action"]}, True
    )
    return [i.get("summary", "") for i in (r.get(entity_id) or {}).get("items", [])]


def todo_add(entity_id, item, description=None):
    data = {"entity_id": entity_id, "item": item}
    if description:
        data["description"] = description
    call_service("todo", "add_item", data)


def create_all_day_event(entity_id, day, summary, description=None):
    """Lager en heldagsavtale (end_date er dagen etter, slik HA forventer)."""
    from datetime import date, timedelta
    d = date.fromisoformat(day) if isinstance(day, str) else day
    data = {
        "entity_id": entity_id,
        "summary": summary,
        "start_date": d.isoformat(),
        "end_date": (d + timedelta(days=1)).isoformat(),
    }
    if description:
        data["description"] = description
    call_service("calendar", "create_event", data)


def todo_remove(entity_id, items):
    """Fjerner varer helt fra en to-do-liste (ikke bare huker av)."""
    if isinstance(items, str):
        items = [items]
    call_service("todo", "remove_item", {"entity_id": entity_id, "item": list(items)})
