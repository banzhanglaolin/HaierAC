"""Climate platform for Haier AC Local."""

from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate.const import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    SWING_BOTH,
    SWING_HORIZONTAL,
    SWING_OFF,
    SWING_VERTICAL,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HaierACConfigEntry
from .client import HaierACClient, HaierACCommunicationError
from .const import DOMAIN, MAX_TEMP, MIN_TEMP
from .protocol import ACStatus, FanDirection, FanSpeed, Mode

FAN_MODE_TO_HAIER = {
    FAN_HIGH: FanSpeed.HIGH,
    FAN_MEDIUM: FanSpeed.MEDIUM,
    FAN_LOW: FanSpeed.LOW,
    FAN_AUTO: FanSpeed.AUTO,
}
HAIER_TO_FAN_MODE = {value: key for key, value in FAN_MODE_TO_HAIER.items()}

SWING_MODE_TO_HAIER = {
    SWING_OFF: FanDirection.OFF,
    SWING_VERTICAL: FanDirection.VERTICAL,
    SWING_HORIZONTAL: FanDirection.HORIZONTAL,
    SWING_BOTH: FanDirection.BOTH,
}
HAIER_TO_SWING_MODE = {value: key for key, value in SWING_MODE_TO_HAIER.items()}

HVAC_MODE_TO_HAIER = {
    HVACMode.AUTO: Mode.PMV,
    HVACMode.COOL: Mode.COOL,
    HVACMode.HEAT: Mode.HEAT,
    HVACMode.FAN_ONLY: Mode.FAN,
    HVACMode.DRY: Mode.DRY,
}
HAIER_TO_HVAC_MODE = {value: key for key, value in HVAC_MODE_TO_HAIER.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaierACConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Haier AC climate entity."""
    client: HaierACClient = entry.runtime_data
    async_add_entities([HaierACClimate(client, entry.entry_id)], update_before_add=True)


class HaierACClimate(ClimateEntity):
    """Climate entity backed by a local Haier AC TCP connection."""

    _attr_has_entity_name = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_WHOLE
    _attr_target_temperature_step = 1
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.AUTO,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.FAN_ONLY,
        HVACMode.DRY,
    ]
    _attr_fan_modes = list(FAN_MODE_TO_HAIER)
    _attr_swing_modes = list(SWING_MODE_TO_HAIER)
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, client: HaierACClient, entry_id: str) -> None:
        self._client = client
        self._attr_name = client.name
        self._attr_unique_id = f"{client.mac.lower()}_climate"
        self._attr_available = True
        self._attr_device_info = {
            "identifiers": {(DOMAIN, client.mac)},
            "manufacturer": "Haier",
            "name": client.name,
        }
        self._entry_id = entry_id

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._status.current_temperature

    @property
    def current_humidity(self) -> float | None:
        """Return the current humidity."""
        return self._status.current_humidity

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        return self._status.target_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        if not self._status.power_on:
            return HVACMode.OFF
        return HAIER_TO_HVAC_MODE.get(self._status.mode, HVACMode.AUTO)

    @property
    def hvac_action(self) -> HVACAction:
        """Return the current HVAC action."""
        if not self._status.power_on:
            return HVACAction.OFF
        if self._status.mode == Mode.FAN:
            return HVACAction.FAN
        return HVACAction.IDLE

    @property
    def fan_mode(self) -> str:
        """Return the fan mode."""
        return HAIER_TO_FAN_MODE.get(self._status.fan_speed, FAN_AUTO)

    @property
    def swing_mode(self) -> str:
        """Return the swing mode."""
        return HAIER_TO_SWING_MODE.get(self._status.fan_direction, SWING_OFF)

    @property
    def _status(self) -> ACStatus:
        return self._client.status

    async def async_update(self) -> None:
        """Fetch latest state from the air conditioner."""
        try:
            await self._client.async_query_status()
        except HaierACCommunicationError:
            self._attr_available = False
        else:
            self._attr_available = True

    async def async_turn_on(self) -> None:
        """Turn the air conditioner on."""
        await self._run_command(self._client.async_turn_on())

    async def async_turn_off(self) -> None:
        """Turn the air conditioner off."""
        await self._run_command(self._client.async_turn_off())

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set a new HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
            return
        await self._run_command(
            self._client.async_apply(mode=HVAC_MODE_TO_HAIER[hvac_mode], power_on=True)
        )

    async def async_set_temperature(self, **kwargs: object) -> None:
        """Set a new target temperature."""
        mode = None
        hvac_mode = _coerce_hvac_mode(kwargs.get("hvac_mode"))
        if hvac_mode in HVAC_MODE_TO_HAIER:
            mode = HVAC_MODE_TO_HAIER[hvac_mode]

        temperature = kwargs.get(ATTR_TEMPERATURE)
        await self._run_command(
            self._client.async_apply(
                mode=mode,
                target_temperature=float(temperature)
                if temperature is not None
                else None,
                power_on=True,
            )
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set a new fan mode."""
        await self._run_command(
            self._client.async_apply(fan_speed=FAN_MODE_TO_HAIER[fan_mode], power_on=True)
        )

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set a new swing mode."""
        await self._run_command(
            self._client.async_apply(
                fan_direction=SWING_MODE_TO_HAIER[swing_mode], power_on=True
            )
        )

    async def _run_command(self, awaitable: object) -> None:
        try:
            await awaitable
        except HaierACCommunicationError:
            self._attr_available = False
            raise
        else:
            self._attr_available = True
            self.async_write_ha_state()


def _coerce_hvac_mode(value: object) -> HVACMode | None:
    if isinstance(value, HVACMode):
        return value
    if isinstance(value, str):
        try:
            return HVACMode(value)
        except ValueError:
            return None
    return None
