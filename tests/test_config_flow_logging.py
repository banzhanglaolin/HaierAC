"""Tests for Haier AC config-flow diagnostics."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


def _install_homeassistant_stubs() -> None:
    """Install enough Home Assistant stubs to import config_flow."""
    voluptuous = types.ModuleType("voluptuous")
    voluptuous.Marker = object
    voluptuous.Optional = lambda *args, **kwargs: args[0]
    voluptuous.Required = lambda *args, **kwargs: args[0]
    voluptuous.All = lambda *args, **kwargs: args
    voluptuous.Coerce = lambda *args, **kwargs: args
    voluptuous.Range = lambda *args, **kwargs: args
    voluptuous.Schema = lambda fields: fields

    homeassistant = types.ModuleType("homeassistant")
    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigFlow:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

    config_entries.ConfigFlow = ConfigFlow

    const = types.ModuleType("homeassistant.const")
    const.CONF_HOST = "host"
    const.CONF_NAME = "name"
    const.CONF_PORT = "port"

    class Platform:
        CLIMATE = "climate"

    const.Platform = Platform

    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict

    homeassistant.config_entries = config_entries
    sys.modules.setdefault("voluptuous", voluptuous)
    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.config_entries", config_entries)
    sys.modules.setdefault("homeassistant.const", const)
    sys.modules.setdefault("homeassistant.data_entry_flow", data_entry_flow)


_install_homeassistant_stubs()
config_flow = importlib.import_module("custom_components.haier_ac.config_flow")


class ConfigFlowLoggingTest(unittest.IsolatedAsyncioTestCase):
    """Exercise connection failure logging used by setup and reconfigure."""

    async def test_connection_failure_is_logged(self) -> None:
        data = {
            "host": "10.16.45.36",
            "port": 56800,
            "mac": "0007A8B26279",
            "timeout": 5,
            "name": "Haier AC",
        }
        client = types.SimpleNamespace(
            async_test_connection=AsyncMock(
                side_effect=config_flow.HaierACCommunicationError("timed out")
            )
        )

        with patch.object(config_flow, "HaierACClient", return_value=client):
            with self.assertLogs(config_flow._LOGGER.name, level="WARNING") as logs:
                errors = await config_flow._test_connection(data)

        self.assertEqual(errors, {"base": "cannot_connect"})
        self.assertIn("10.16.45.36:56800", logs.output[0])
        self.assertIn("0007A8B26279", logs.output[0])
        self.assertIn("timed out", logs.output[0])

    async def test_discovery_defaults_use_first_device(self) -> None:
        devices = [types.SimpleNamespace(host="10.16.45.36", mac="0007A8B26279")]

        with patch.object(config_flow, "async_discover_devices", AsyncMock(return_value=devices)):
            defaults = await config_flow._discover_defaults()

        self.assertEqual(defaults, {"host": "10.16.45.36", "mac": "0007A8B26279"})

    async def test_discovery_defaults_ignore_discovery_failure(self) -> None:
        with patch.object(
            config_flow,
            "async_discover_devices",
            AsyncMock(side_effect=config_flow.HaierACDiscoveryError("no socket")),
        ):
            defaults = await config_flow._discover_defaults()

        self.assertEqual(defaults, {})

if __name__ == "__main__":
    unittest.main()
