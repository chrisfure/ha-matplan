"""Huskelappen – oppstart."""
import logging
import sys
import threading
import time

import actions
import db
import logic
import mailer
import server

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    stream=sys.stdout)


def clock_loop():
    """Hvert minutt: varsler til riktig tid og sensorene til veggskjermen."""
    time.sleep(10)
    while True:
        try:
            logic.tick()
        except Exception as e:
            logging.getLogger("huskelappen").warning("Klokkejobb feilet: %s", e)
        logic.push_sensors()
        time.sleep(60)


if __name__ == "__main__":
    db.conn()
    mailer.start_scheduler()
    actions.start()
    threading.Thread(target=clock_loop, daemon=True, name="clock").start()
    server.run()
