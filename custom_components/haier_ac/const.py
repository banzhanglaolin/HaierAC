"""Constants for the Haier AC Local integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "haier_ac"
PLATFORMS = (Platform.CLIMATE,)

CONF_MAC = "mac"
CONF_TIMEOUT = "timeout"

DEFAULT_NAME = "Haier AC"
DEFAULT_PORT = 56800
DEFAULT_TIMEOUT = 5

MIN_TEMP = 16
MAX_TEMP = 30
