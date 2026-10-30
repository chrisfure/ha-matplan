"""Lytter etter knappetrykk i varsler (mobile_app_notification_action) via Home Assistant sitt websocket-API."""
import json
import logging
import os
import socket
import threading
import time

import logic
import wsclient
from config import HA_TOKEN

log = logging.getLogger("huskelappen.actions")
WS_URL = os.environ.get("HA_WS_URL", "ws://supervisor/core/websocket")
status = {"connected": False, "error": None}


def _run_once():
    ws = wsclient.create_connection(WS_URL, timeout=30)
    try:
        hello = json.loads(ws.recv())
        if hello.get("type") == "auth_required":
            ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
            res = json.loads(ws.recv())
            if res.get("type") != "auth_ok":
                raise RuntimeError(f"Innlogging mot Home Assistant feilet: {res.get('message') or res}")
        ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "mobile_app_notification_action"}))
        ws.settimeout(120)
        status.update(connected=True, error=None)
        log.info("Lytter etter knapper i varsler")
        msg_id, quiet = 1, 0
        while True:
            try:
                raw = ws.recv()
                quiet = 0
            except socket.timeout:
                # stille linje: ping HA, og koble opp på nytt hvis den heller ikke svarer
                quiet += 1
                if quiet > 1:
                    raise RuntimeError("Ingen svar fra Home Assistant")
                msg_id += 1
                ws.send(json.dumps({"id": msg_id, "type": "ping"}))
                continue
            if not raw:
                raise RuntimeError("Forbindelsen ble lukket")
            msg = json.loads(raw)
            if msg.get("type") != "event":
                continue
            action = ((msg.get("event") or {}).get("data") or {}).get("action")
            if action == logic.ACTION_APPROVE_ALL:
                try:
                    log.info("Knapp i varsel: %s", logic.handle_action(action))
                except Exception as e:
                    log.warning("Knapp i varsel feilet: %s", e)
    finally:
        status["connected"] = False
        try:
            ws.close()
        except Exception:
            pass


def start():
    def loop():
        time.sleep(5)
        delay = 5
        while True:
            started = time.time()
            try:
                _run_once()
            except Exception as e:
                status["error"] = str(e)
                log.warning("Websocket: %s (prøver igjen om %s s)", e, delay)
            delay = 5 if time.time() - started > 60 else min(delay * 2, 300)
            time.sleep(delay)

    threading.Thread(target=loop, daemon=True, name="actions").start()
