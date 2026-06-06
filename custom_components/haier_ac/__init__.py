"""Haier AC Local integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .client import HaierACClient

    HaierACConfigEntry: TypeAlias = ConfigEntry[HaierACClient]
else:
    HomeAssistant = Any
    HaierACConfigEntry: TypeAlias = Any



async def async_setup_entry(hass: HomeAssistant, entry: HaierACConfigEntry) -> bool:
    """Set up Haier AC Local from a config entry."""
    from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT

    from .client import HaierACClient
    from .const import CONF_MAC, CONF_TIMEOUT, DEFAULT_NAME, DEFAULT_TIMEOUT, PLATFORMS

    client = HaierACClient(
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        mac=entry.data[CONF_MAC],
        timeout=entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
        name=entry.data.get(CONF_NAME, DEFAULT_NAME),
    )

    entry.runtime_data = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HaierACConfigEntry) -> bool:
    """Unload a config entry."""
    from .const import PLATFORMS

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
