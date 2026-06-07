"""Tests for Haier AC integration-level discovery."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


def _install_homeassistant_stubs() -> None:
    """Install enough Home Assistant stubs to call integration discovery."""
    homeassistant = sys.modules.setdefault(
        "homeassistant", types.ModuleType("homeassistant")
    )

    config_entries = sys.modules.setdefault(
        "homeassistant.config_entries",
        types.ModuleType("homeassistant.config_entries"),
    )
    config_entries.SOURCE_INTEGRATION_DISCOVERY = "integration_discovery"

    const = sys.modules.setdefault(
        "homeassistant.const", types.ModuleType("homeassistant.const")
    )
    const.CONF_HOST = "host"
    const.CONF_NAME = "name"
    const.CONF_PORT = "port"

    class Platform:
        CLIMATE = "climate"

    const.Platform = Platform
    homeassistant.config_entries = config_entries


_install_homeassistant_stubs()
integration = importlib.import_module("custom_components.haier_ac")


class IntegrationDiscoveryTest(unittest.IsolatedAsyncioTestCase):
    """Exercise automatic config-flow creation from UDP discovery."""

    async def test_discover_new_devices_starts_integration_discovery_flow(self) -> None:
        device = types.SimpleNamespace(
            host="10.16.45.36",
            mac="0007A8B26279",
            module_type="eSDK_WIFI_AC",
            firmware_version="e_1.3.03G_1.0.00",
        )
        hass = _Hass()

        with patch(
            "custom_components.haier_ac.discovery.async_discover_devices",
            AsyncMock(return_value=[device]),
        ):
            await integration._async_discover_new_devices(hass)
        await asyncio.gather(*hass.tasks)

        hass.config_entries.flow.async_init.assert_awaited_once_with(
            "haier_ac",
            context={"source": "integration_discovery"},
            data={
                "name": "Haier AC",
                "host": "10.16.45.36",
                "port": 56800,
                "mac": "0007A8B26279",
                "timeout": 5,
                "discovered_device_info": "e_1.3.03G_1.0.00eSDK_WIFI_AC",
            },
        )

    async def test_discover_new_devices_skips_configured_mac(self) -> None:
        device = types.SimpleNamespace(
            host="10.16.45.36",
            mac="0007A8B26279",
            module_type=None,
            firmware_version=None,
        )
        entry = types.SimpleNamespace(data={"mac": "00:07:a8:b2:62:79"})
        hass = _Hass(entries=[entry])

        with patch(
            "custom_components.haier_ac.discovery.async_discover_devices",
            AsyncMock(return_value=[device]),
        ):
            await integration._async_discover_new_devices(hass)

        hass.config_entries.flow.async_init.assert_not_called()


class _Hass:
    def __init__(self, entries: list[object] | None = None) -> None:
        self.data: dict[str, object] = {}
        self.tasks: list[asyncio.Task] = []
        self.config_entries = _ConfigEntries(entries or [])

    def async_create_task(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task


class _ConfigEntries:
    def __init__(self, entries: list[object]) -> None:
        self._entries = entries
        self.flow = types.SimpleNamespace(
            async_init=AsyncMock(return_value={"type": "form"})
        )

    def async_entries(self, domain: str) -> list[object]:
        return self._entries


if __name__ == "__main__":
    unittest.main()
