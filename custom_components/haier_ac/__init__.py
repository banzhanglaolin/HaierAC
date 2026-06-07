"""Haier AC Local integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .client import HaierACClient

    HaierACConfigEntry: TypeAlias = ConfigEntry[HaierACClient]
else:
    HomeAssistant = Any
    HaierACConfigEntry: TypeAlias = Any


_LOGGER = logging.getLogger(__name__)
_DISCOVERED_DEVICE_INFO = "discovered_device_info"
_DISCOVERY_TASK = "discovery_task"


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up Haier AC Local and discover devices on the local network."""
    _async_schedule_discovery(hass)
    return True


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
    _async_schedule_discovery(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HaierACConfigEntry) -> bool:
    """Unload a config entry."""
    from .const import PLATFORMS

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _async_schedule_discovery(hass: HomeAssistant) -> None:
    """Schedule one background UDP discovery scan."""
    from .const import DOMAIN

    domain_data = hass.data.setdefault(DOMAIN, {})
    task = domain_data.get(_DISCOVERY_TASK)
    if task is not None and not task.done():
        return
    domain_data[_DISCOVERY_TASK] = hass.async_create_task(
        _async_discover_new_devices(hass)
    )


async def _async_discover_new_devices(hass: HomeAssistant) -> None:
    """Start config flows for Haier AC devices discovered by UDP broadcast."""
    from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
    from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT

    from .const import (
        CONF_MAC,
        CONF_TIMEOUT,
        DEFAULT_NAME,
        DEFAULT_PORT,
        DEFAULT_TIMEOUT,
        DOMAIN,
    )
    from .discovery import HaierACDiscoveryError, async_discover_devices

    try:
        devices = await async_discover_devices()
    except HaierACDiscoveryError as err:
        _LOGGER.debug("Could not run Haier AC UDP discovery: %s", err)
        return

    configured_macs = _configured_macs(hass)
    for device in devices:
        if device.mac in configured_macs:
            continue

        data: dict[str, Any] = {
            CONF_NAME: DEFAULT_NAME,
            CONF_HOST: device.host,
            CONF_PORT: DEFAULT_PORT,
            CONF_MAC: device.mac,
            CONF_TIMEOUT: DEFAULT_TIMEOUT,
        }
        if device_info := _format_discovered_device_info(device):
            data[_DISCOVERED_DEVICE_INFO] = device_info

        _LOGGER.info(
            "Starting Haier AC discovery flow for %s with MAC %s",
            device.host,
            device.mac,
        )
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_INTEGRATION_DISCOVERY},
                data=data,
            )
        )


def _configured_macs(hass: HomeAssistant) -> set[str]:
    """Return MAC addresses that already have config entries."""
    from .const import CONF_MAC, DOMAIN
    from .protocol import InvalidPacketError, normalize_mac

    configured: set[str] = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        mac = entry.data.get(CONF_MAC)
        if mac is None:
            continue
        try:
            configured.add(normalize_mac(str(mac)))
        except InvalidPacketError:
            continue
    return configured


def _format_discovered_device_info(device: Any) -> str | None:
    """Return read-only firmware/module information for the discovery flow."""
    firmware_version = getattr(device, "firmware_version", None)
    module_type = getattr(device, "module_type", None)
    if firmware_version and module_type:
        return f"{firmware_version}{module_type}"
    return firmware_version or module_type
