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

        async def async_set_unique_id(self, unique_id, *args, **kwargs):
            self._unique_id = unique_id

        def _abort_if_unique_id_configured(self, *args, **kwargs):
            self._abort_updates = kwargs.get("updates")

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

        def async_create_entry(self, **kwargs):
            return {"type": "create_entry", **kwargs}

        def async_abort(self, **kwargs):
            return {"type": "abort", **kwargs}

        def _set_confirm_only(self):
            self._confirm_only = True

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

    helpers = types.ModuleType("homeassistant.helpers")
    config_entry_flow = types.ModuleType("homeassistant.helpers.config_entry_flow")
    config_entry_flow.registered_discovery_flows = []

    def register_discovery_flow(domain, name, callback):
        config_entry_flow.registered_discovery_flows.append((domain, name, callback))

    config_entry_flow.register_discovery_flow = register_discovery_flow
    helpers.config_entry_flow = config_entry_flow

    homeassistant.config_entries = config_entries
    homeassistant.helpers = helpers
    sys.modules.setdefault("voluptuous", voluptuous)
    sys.modules.setdefault("homeassistant", homeassistant)
    sys.modules.setdefault("homeassistant.config_entries", config_entries)
    sys.modules.setdefault("homeassistant.const", const)
    sys.modules.setdefault("homeassistant.data_entry_flow", data_entry_flow)
    sys.modules.setdefault("homeassistant.helpers", helpers)
    sys.modules.setdefault("homeassistant.helpers.config_entry_flow", config_entry_flow)


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
        devices = [
            types.SimpleNamespace(
                host="10.16.45.36",
                mac="0007A8B26279",
                name="Xing_Qi_Wu",
                module_type="eSDK_WIFI_AC",
                firmware_version="e_1.3.03G_1.0.00",
            )
        ]

        with patch.object(config_flow, "async_discover_devices", AsyncMock(return_value=devices)):
            defaults = await config_flow._discover_defaults()

        self.assertEqual(
            defaults,
            {
                "host": "10.16.45.36",
                "mac": "0007A8B26279",
                "discovered_device_info": "e_1.3.03G_1.0.00eSDK_WIFI_AC",
            },
        )

    async def test_discovery_defaults_do_not_prefill_name(self) -> None:
        devices = [
            types.SimpleNamespace(
                host="10.16.45.36",
                mac="0007A8B26279",
                name="Xing_Qi_Wu",
                module_type="eSDK_WIFI_AC",
                firmware_version="e_1.3.03G_1.0.00",
            )
        ]

        with patch.object(config_flow, "async_discover_devices", AsyncMock(return_value=devices)):
            defaults = await config_flow._discover_defaults()

        self.assertNotIn("name", defaults)

    def test_description_placeholders_show_discovered_device_info(self) -> None:
        placeholders = config_flow._description_placeholders(
            {"discovered_device_info": "e_1.3.03G_1.0.00eSDK_WIFI_AC"}
        )

        self.assertEqual(
            placeholders,
            {"discovered_device_info": "e_1.3.03G_1.0.00eSDK_WIFI_AC"},
        )

    def test_timeout_schema_uses_text_input(self) -> None:
        schema = config_flow._data_schema()

        self.assertIs(schema["timeout"], str)

    def test_validate_user_input_accepts_timeout_text(self) -> None:
        data, errors = config_flow._validate_user_input(
            {
                "host": "10.16.45.36",
                "port": 56800,
                "mac": "0007A8B26279",
                "timeout": "5",
                "name": "Haier AC",
            }
        )

        self.assertEqual(errors, {})
        self.assertEqual(data["timeout"], 5)

    def test_validate_user_input_rejects_invalid_timeout_text(self) -> None:
        _, errors = config_flow._validate_user_input(
            {
                "host": "10.16.45.36",
                "port": 56800,
                "mac": "0007A8B26279",
                "timeout": "fast",
                "name": "Haier AC",
            }
        )

        self.assertEqual(errors, {"timeout": "invalid_timeout"})

    def test_validate_user_input_rejects_out_of_range_timeout(self) -> None:
        _, errors = config_flow._validate_user_input(
            {
                "host": "10.16.45.36",
                "port": 56800,
                "mac": "0007A8B26279",
                "timeout": "31",
                "name": "Haier AC",
            }
        )

        self.assertEqual(errors, {"timeout": "invalid_timeout"})

    async def test_discovery_defaults_ignore_discovery_failure(self) -> None:
        with patch.object(
            config_flow,
            "async_discover_devices",
            AsyncMock(side_effect=config_flow.HaierACDiscoveryError("no socket")),
        ):
            defaults = await config_flow._discover_defaults()

        self.assertEqual(defaults, {})

    async def test_integration_discovery_shows_confirm_form(self) -> None:
        flow = config_flow.HaierACConfigFlow()

        result = await flow.async_step_integration_discovery(
            {
                "host": "10.16.45.36",
                "port": 56800,
                "mac": "00:07:a8:b2:62:79",
                "timeout": 5,
                "discovered_device_info": "e_1.3.03G_1.0.00eSDK_WIFI_AC",
            }
        )

        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "discovery_confirm")
        self.assertEqual(flow._unique_id, "0007A8B26279")
        self.assertTrue(flow._confirm_only)
        self.assertEqual(
            result["description_placeholders"],
            {
                "host": "10.16.45.36",
                "mac": "0007A8B26279",
                "discovered_device_info": "e_1.3.03G_1.0.00eSDK_WIFI_AC",
            },
        )

    async def test_discovery_confirm_creates_entry_after_connection_test(self) -> None:
        flow = config_flow.HaierACConfigFlow()
        await flow.async_step_integration_discovery(
            {
                "host": "10.16.45.36",
                "mac": "0007A8B26279",
            }
        )

        with patch.object(config_flow, "_test_connection", AsyncMock(return_value={})):
            result = await flow.async_step_discovery_confirm({})

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["title"], "Haier AC")
        self.assertEqual(
            result["data"],
            {
                "host": "10.16.45.36",
                "port": 56800,
                "mac": "0007A8B26279",
                "timeout": 5,
                "name": "Haier AC",
            },
        )

    async def test_discovery_confirm_shows_connection_error(self) -> None:
        flow = config_flow.HaierACConfigFlow()
        await flow.async_step_integration_discovery(
            {
                "host": "10.16.45.36",
                "mac": "0007A8B26279",
            }
        )

        with patch.object(
            config_flow,
            "_test_connection",
            AsyncMock(return_value={"base": "cannot_connect"}),
        ):
            result = await flow.async_step_discovery_confirm({})

        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "discovery_confirm")
        self.assertEqual(result["errors"], {"base": "cannot_connect"})

    async def test_async_has_devices_uses_udp_discovery(self) -> None:
        with patch.object(
            config_flow,
            "async_discover_devices",
            AsyncMock(return_value=[object()]),
        ):
            self.assertTrue(await config_flow._async_has_devices(object()))

    async def test_async_has_devices_ignores_discovery_failure(self) -> None:
        with patch.object(
            config_flow,
            "async_discover_devices",
            AsyncMock(side_effect=config_flow.HaierACDiscoveryError("no socket")),
        ):
            self.assertFalse(await config_flow._async_has_devices(object()))

if __name__ == "__main__":
    unittest.main()
