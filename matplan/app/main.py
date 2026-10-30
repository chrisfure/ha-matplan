"""Matplan – oppstart."""
import logging
import sys

import threading
import time

import db
import logic
import mailer
import server


def sensor_loop():
    """Holder «Dagligvarer denne måneden» i Home Assistant oppdatert (også etter omstart av HA)."""
    import economy
    time.sleep(20)
    while True:
        economy.push_sensor()
        time.sleep(15 * 60)


def calendar_sync_loop():
    time.sleep(10)
    while True:
        try:
            logic.sync_dinner_calendar()
        except Exception as e:
            logging.getLogger("matplan").warning("Synk av middagskalender feilet: %s", e)
        time.sleep(6 * 3600)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

if __name__ == "__main__":
    db.conn()
    mailer.start_scheduler()
    threading.Thread(target=calendar_sync_loop, daemon=True, name="calendar-sync").start()
    threading.Thread(target=sensor_loop, daemon=True, name="sensor").start()
    server.run()
