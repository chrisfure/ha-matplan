"""Værvarsel fra Home Assistant og enkle klesråd for barnehage- og skoledagen."""
import logging
import time
from collections import Counter
from datetime import datetime

import db
import ha
import util

log = logging.getLogger("huskelappen.weather")

_cache = {}
CACHE_SECONDS = 1800
DAY_HOURS = range(7, 17)      # dagen ute: kl. 07–16
MORNING_LAST_HOUR = 9         # temperaturen ved levering avgjør påkledningen

CONDITIONS = {
    "sunny": "sol", "clear-night": "klart", "partlycloudy": "delvis skyet", "cloudy": "skyet",
    "rainy": "regn", "pouring": "kraftig regn", "snowy": "snø", "snowy-rainy": "sludd",
    "lightning": "torden", "lightning-rainy": "torden og regn", "fog": "tåke", "hail": "hagl",
    "windy": "vind", "windy-variant": "vind og skyer", "exceptional": "uvanlig vær",
}
WET = {"rainy", "pouring", "snowy", "snowy-rainy", "lightning-rainy", "hail"}
WIND_FACTOR = {"m/s": 1.0, "km/h": 1 / 3.6, "mph": 0.447, "kn": 0.514, "ft/s": 0.305}


def _cached(key, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def _forecast(entity, kind):
    def get():
        resp = ha.call_service("weather", "get_forecasts", {"entity_id": entity, "type": kind}, True)
        return (resp.get(entity) or {}).get("forecast") or []
    return _cached((entity, kind), get)


def _wind_factor(entity):
    def get():
        try:
            st = ha._req("GET", f"/states/{entity}") or {}
            return WIND_FACTOR.get(st.get("attributes", {}).get("wind_speed_unit"), 1 / 3.6)
        except Exception:
            return 1 / 3.6
    return _cached((entity, "unit"), get)


def _local(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(util.tz())


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def day_forecast(d, entity=None):
    """Vær for dagen (kl. 07–16) som et lite oppslag, eller None."""
    entity = entity or db.get_setting("weather_entity")
    if not entity:
        return None
    try:
        wf = _wind_factor(entity)
        try:
            hourly = _forecast(entity, "hourly")
        except ha.HAError:
            hourly = []
        hours = [h for h in hourly if h.get("datetime") and h.get("temperature") is not None
                 and _local(h["datetime"]).date() == d and _local(h["datetime"]).hour in DAY_HOURS]
        if len(hours) >= 3:
            temps = [_num(h["temperature"]) for h in hours]
            morning = [_num(h["temperature"]) for h in hours
                       if _local(h["datetime"]).hour <= MORNING_LAST_HOUR] or temps
            return {
                "tmin": min(morning), "tmax": max(temps),
                "precip": sum(_num(h.get("precipitation")) for h in hours),
                "prob": max(_num(h.get("precipitation_probability")) for h in hours),
                "wind": max(_num(h.get("wind_speed")) for h in hours) * wf,
                "conditions": [h.get("condition") for h in hours if h.get("condition")],
            }
        for f in _forecast(entity, "daily"):
            if f.get("datetime") and _local(f["datetime"]).date() == d:
                tmax = _num(f.get("temperature"))
                return {
                    "tmin": _num(f.get("templow"), tmax), "tmax": tmax,
                    "precip": _num(f.get("precipitation")),
                    "prob": _num(f.get("precipitation_probability")),
                    "wind": _num(f.get("wind_speed")) * wf,
                    "conditions": [f["condition"]] if f.get("condition") else [],
                }
    except Exception as e:  # HA nede, ukjent entitet osv. – da hopper vi bare over været
        log.warning("Kunne ikke hente værvarsel: %s", e)
    return None


def advice(fc):
    """(«2–6°, regn 4 mm», «ullundertøy, …; regntøy og gummistøvler»)"""
    t = fc["tmin"]
    conds = set(fc["conditions"])
    wet = fc["precip"] >= 0.5 or fc["prob"] >= 60 or bool(conds & WET)
    snow = bool(conds & {"snowy", "snowy-rainy"}) or (wet and t <= 0.5)

    clothes = []
    if t <= -8:
        clothes.append("ull innerst og ullmellomlag, vinterdress, ullsokker, tykke votter og lue")
    elif t <= -2:
        clothes.append("ullundertøy, vinterdress, votter og lue")
    elif t <= 4:
        clothes.append("ullundertøy, fôret dress eller termo, lue og votter")
    elif t <= 10:
        clothes.append("ull eller fleece under, lue og tynne votter")
    elif t <= 16:
        clothes.append("lett jakke eller fleece")
    else:
        clothes.append("lett tøy")
    if wet:
        if snow:
            clothes.append("vanntette votter og ekstra votter i sekken")
        else:
            clothes.append("regntøy og gummistøvler" + (" (fôrede)" if t <= 4 else ""))
            if t <= 4:
                clothes.append("ekstra votter")
    if fc["wind"] >= 8:
        clothes.append("vindtett ytterlag")
    if t > 14 and not wet and "sunny" in conds:
        clothes.append("solhatt og solkrem")

    lo, hi = round(fc["tmin"]), round(fc["tmax"])
    summary = f"{lo}°" if lo == hi else f"{lo}–{hi}°"
    if fc["conditions"]:
        main = Counter(fc["conditions"]).most_common(1)[0][0]
        summary += f", {CONDITIONS.get(main, main)}"
    if fc["precip"] >= 0.5:
        summary += f" ({fc['precip']:.0f} mm)"
    if fc["wind"] >= 8:
        summary += f", vind {fc['wind']:.0f} m/s"
    return summary, "; ".join(clothes)


def day_advice(d):
    fc = day_forecast(d)
    return advice(fc) if fc else None
