"""Climate platform for Haier AC Local."""

from __future__ import annotations

import asyncio

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
from homeassistant.core import HomeAssistant, callback
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
FAN_ONLY_FAN_MODES = [FAN_HIGH, FAN_MEDIUM, FAN_LOW]

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
_STATUS_REPORT_INTERVAL = 5
_MAX_AVAILABILITY_FAILURES = 3


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
        self._communication_failures = 0
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
        if (
            self._status.mode == Mode.FAN
            and self._status.fan_speed == FanSpeed.AUTO
        ):
            return FAN_HIGH
        return HAIER_TO_FAN_MODE.get(self._status.fan_speed, FAN_AUTO)

    @property
    def fan_modes(self) -> list[str]:
        """Return available fan modes."""
        if self.hvac_mode == HVACMode.FAN_ONLY:
            return FAN_ONLY_FAN_MODES
        return list(FAN_MODE_TO_HAIER)

    @property
    def swing_mode(self) -> str:
        """Return the swing mode."""
        return HAIER_TO_SWING_MODE.get(self._status.fan_direction, SWING_OFF)

    @property
    def _status(self) -> ACStatus:
        return self._client.status

    async def async_added_to_hass(self) -> None:
        """Start consuming unsolicited status reports from the TCP connection."""
        self.async_on_remove(
            self._client.async_add_status_listener(
                self._handle_client_status_update
            )
        )
        keepalive_task = self.hass.async_create_task(self._async_status_report_loop())
        self.async_on_remove(keepalive_task.cancel)

    async def async_update(self) -> None:
        """Fetch latest state from the air conditioner."""
        try:
            await self._client.async_query_status()
        except HaierACCommunicationError:
            self._record_communication_failure()
        else:
            self._mark_available()

    @callback
    def _handle_client_status_update(self, status: ACStatus) -> None:
        """Write state immediately when the client receives a status report."""
        self._mark_available()
        self.async_write_ha_state()

    async def _async_status_report_loop(self) -> None:
        while True:
            await asyncio.sleep(_STATUS_REPORT_INTERVAL)
            try:
                await self._client.async_heartbeat()
            except asyncio.CancelledError:
                raise
            except HaierACCommunicationError:
                if self._record_communication_failure():
                    self.async_write_ha_state()
            else:
                if self._mark_available():
                    self.async_write_ha_state()

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
        fan_speed = FAN_MODE_TO_HAIER[fan_mode]
        if self.hvac_mode == HVACMode.FAN_ONLY and fan_speed == FanSpeed.AUTO:
            fan_speed = FanSpeed.HIGH
        await self._run_command(
            self._client.async_apply(fan_speed=fan_speed, power_on=True)
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
            if self._record_communication_failure():
                self.async_write_ha_state()
            raise
        else:
            self._mark_available()
            self.async_write_ha_state()

    def _mark_available(self) -> bool:
        was_unavailable = not self._attr_available
        self._communication_failures = 0
        self._attr_available = True
        return was_unavailable

    def _record_communication_failure(self) -> bool:
        self._communication_failures += 1
        if self._communication_failures < _MAX_AVAILABILITY_FAILURES:
            return False
        was_available = self._attr_available
        self._attr_available = False
        return was_available


def _coerce_hvac_mode(value: object) -> HVACMode | None:
    if isinstance(value, HVACMode):
        return value
    if isinstance(value, str):
        try:
            return HVACMode(value)
        except ValueError:
            return None
    return None
