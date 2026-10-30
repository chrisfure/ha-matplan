"""Konfigurasjon for Huskelappen. Leser add-on-innstillinger og miljøvariabler."""
import json
import os

OPTIONS_PATH = os.environ.get("HUSK_OPTIONS", "/data/options.json")
DATA_DIR = os.environ.get("HUSK_DATA", "/data")
MEDIA_DIR = os.environ.get("HUSK_MEDIA", "/media/huskelappen")
# Sti slik Home Assistant sin media-kilde ser filene i MEDIA_DIR
MEDIA_SOURCE_PREFIX = os.environ.get(
    "HUSK_MEDIA_SOURCE", "media-source://media_source/local/huskelappen"
)

HA_URL = os.environ.get("HA_URL", "http://supervisor/core/api")
HA_TOKEN = os.environ.get("HA_TOKEN") or os.environ.get("SUPERVISOR_TOKEN", "")
SUPERVISOR_URL = os.environ.get("SUPERVISOR_URL", "http://supervisor")

PORT = int(os.environ.get("HUSK_PORT", "8098"))
DEV = os.environ.get("HUSK_DEV") == "1"
INGRESS_IP = "172.30.32.2"


def load_options() -> dict:
    try:
        with open(OPTIONS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
