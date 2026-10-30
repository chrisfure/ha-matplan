"""Konfigurasjon for Matplan. Leser add-on-innstillinger og miljøvariabler."""
import json
import os

OPTIONS_PATH = os.environ.get("MATPLAN_OPTIONS", "/data/options.json")
DATA_DIR = os.environ.get("MATPLAN_DATA", "/data")
MEDIA_DIR = os.environ.get("MATPLAN_MEDIA", "/media/matplan")
# Sti slik Home Assistant sin media-kilde ser filene i MEDIA_DIR
MEDIA_SOURCE_PREFIX = os.environ.get(
    "MATPLAN_MEDIA_SOURCE", "media-source://media_source/local/matplan"
)

# Inne i en add-on snakker vi med HA via Supervisor-proxyen.
HA_URL = os.environ.get("HA_URL", "http://supervisor/core/api")
HA_TOKEN = os.environ.get("HA_TOKEN") or os.environ.get("SUPERVISOR_TOKEN", "")

PORT = int(os.environ.get("MATPLAN_PORT", "8099"))
# Utvikling: tillat forespørsler som ikke kommer via HA ingress
DEV = os.environ.get("MATPLAN_DEV") == "1"
INGRESS_IP = "172.30.32.2"


def load_options() -> dict:
    try:
        with open(OPTIONS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
