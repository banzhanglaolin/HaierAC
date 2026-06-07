"""Tests for the Haier AC climate entity."""

from __future__ import annotations

import enum
import importlib
import sys
import types
import unittest
from unittest.mock import AsyncMock

from custom_components.haier_ac.protocol import ACStatus, FanSpeed, Mode


def _install_homeassistant_stubs() -> None:
    """Install enough Home Assistant stubs to import the climate platform."""
    homeassistant = sys.modules.setdefault(
        "homeassistant", types.ModuleType("homeassistant")
    )

    components = sys.modules.setdefault(
        "homeassistant.components", types.ModuleType("homeassistant.components")
    )
    climate = sys.modules.setdefault(
        "homeassistant.components.climate",
        types.ModuleType("homeassistant.components.climate"),
    )

    class ClimateEntity:
        def async_write_ha_state(self) -> None:
            self.wrote_state = True

    class ClimateEntityFeature(enum.IntFlag):
        TARGET_TEMPERATURE = 1
        FAN_MODE = 2
        SWING_MODE = 4
        TURN_ON = 8
        TURN_OFF = 16

    class HVACAction(str, enum.Enum):
        OFF = "off"
        IDLE = "idle"
        FAN = "fan"

    class HVACMode(str, enum.Enum):
        OFF = "off"
        AUTO = "auto"
        COOL = "cool"
        HEAT = "heat"
        FAN_ONLY = "fan_only"
        DRY = "dry"

    climate.ClimateEntity = ClimateEntity
    climate.ClimateEntityFeature = ClimateEntityFeature
    climate.HVACAction = HVACAction
    climate.HVACMode = HVACMode

    climate_const = sys.modules.setdefault(
        "homeassistant.components.climate.const",
        types.ModuleType("homeassistant.components.climate.const"),
    )
    climate_const.FAN_AUTO = "auto"
    climate_const.FAN_HIGH = "high"
    climate_const.FAN_LOW = "low"
    climate_const.FAN_MEDIUM = "medium"
    climate_const.SWING_BOTH = "both"
    climate_const.SWING_HORIZONTAL = "horizontal"
    climate_const.SWING_OFF = "off"
    climate_const.SWING_VERTICAL = "vertical"

    const = sys.modules.setdefault(
        "homeassistant.const", types.ModuleType("homeassistant.const")
    )
    const.ATTR_TEMPERATURE = "temperature"
    const.CONF_HOST = "host"
    const.CONF_NAME = "name"
    const.CONF_PORT = "port"
    const.PRECISION_WHOLE = 1

    class UnitOfTemperature(str, enum.Enum):
        CELSIUS = "C"

    class Platform(str, enum.Enum):
        CLIMATE = "climate"

    const.Platform = Platform
    const.UnitOfTemperature = UnitOfTemperature

    core = sys.modules.setdefault(
        "homeassistant.core", types.ModuleType("homeassistant.core")
    )
    core.HomeAssistant = object

    helpers = sys.modules.setdefault(
        "homeassistant.helpers", types.ModuleType("homeassistant.helpers")
    )
    entity_platform = sys.modules.setdefault(
        "homeassistant.helpers.entity_platform",
        types.ModuleType("homeassistant.helpers.entity_platform"),
    )
    entity_platform.AddEntitiesCallback = object
    helpers.entity_platform = entity_platform
    homeassistant.components = components


_install_homeassistant_stubs()
climate = importlib.import_module("custom_components.haier_ac.climate")


class ClimateFanModeTest(unittest.IsolatedAsyncioTestCase):
    """Exercise fan mode behavior exposed to Home Assistant."""

    def _entity(self, status: ACStatus) -> object:
        client = types.SimpleNamespace(
            name="Haier AC",
            mac="AABBCCDDEEFF",
            status=status,
            async_apply=AsyncMock(return_value=status),
            async_query_status=AsyncMock(return_value=status),
        )
        return climate.HaierACClimate(client, "entry-id")

    def test_fan_only_modes_exclude_auto(self) -> None:
        entity = self._entity(
            ACStatus(power_on=True, mode=Mode.FAN, fan_speed=FanSpeed.HIGH)
        )

        self.assertEqual(
            entity.fan_modes,
            [climate.FAN_HIGH, climate.FAN_MEDIUM, climate.FAN_LOW],
        )

    def test_non_fan_only_modes_include_auto(self) -> None:
        entity = self._entity(
            ACStatus(power_on=True, mode=Mode.COOL, fan_speed=FanSpeed.AUTO)
        )

        self.assertIn(climate.FAN_AUTO, entity.fan_modes)

    def test_fan_only_auto_status_is_reported_as_high(self) -> None:
        entity = self._entity(
            ACStatus(power_on=True, mode=Mode.FAN, fan_speed=FanSpeed.AUTO)
        )

        self.assertEqual(entity.fan_mode, climate.FAN_HIGH)

    async def test_fan_only_auto_request_is_sent_as_high(self) -> None:
        entity = self._entity(
            ACStatus(power_on=True, mode=Mode.FAN, fan_speed=FanSpeed.HIGH)
        )

        await entity.async_set_fan_mode(climate.FAN_AUTO)

        entity._client.async_apply.assert_awaited_once_with(
            fan_speed=FanSpeed.HIGH,
            power_on=True,
        )

    async def test_update_marks_entity_unavailable_on_communication_error(self) -> None:
        entity = self._entity(
            ACStatus(power_on=True, mode=Mode.COOL, fan_speed=FanSpeed.AUTO)
        )
        entity._client.async_query_status.side_effect = (
            climate.HaierACCommunicationError("missed heartbeats")
        )

        await entity.async_update()

        self.assertFalse(entity._attr_available)


if __name__ == "__main__":
    unittest.main()
