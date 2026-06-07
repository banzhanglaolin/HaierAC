"""Config flow for Haier AC Local."""

from __future__ import annotations

from ipaddress import ip_address
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.data_entry_flow import FlowResult

from .client import HaierACClient, HaierACCommunicationError
from .const import (
    CONF_MAC,
    CONF_TIMEOUT,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    DOMAIN,
)
from .discovery import HaierACDiscoveryError, async_discover_devices
from .protocol import InvalidPacketError, normalize_mac

_LOGGER = logging.getLogger(__name__)
_DISCOVERED_DEVICE_INFO = "discovered_device_info"
_DISCOVERED_DEVICE_INFO_NONE = "Not discovered"


class HaierACConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Haier AC Local."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_data: dict[str, Any] | None = None
        self._discovery_placeholders: dict[str, str] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        defaults: dict[str, Any] | None = None

        if user_input is not None:
            data, errors = _validate_user_input(user_input)
            if not errors:
                errors = await _test_connection(data)
            if not errors:
                await self.async_set_unique_id(data[CONF_MAC])
                self._abort_if_unique_id_configured(
                    updates={
                        CONF_HOST: data[CONF_HOST],
                        CONF_PORT: data[CONF_PORT],
                    }
                )
                return self.async_create_entry(
                    title=data[CONF_NAME],
                    data=data,
                )
        else:
            defaults = await _discover_defaults()

        return self.async_show_form(
            step_id="user",
            data_schema=_data_schema(defaults),
            description_placeholders=_description_placeholders(defaults),
            errors=errors,
        )

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> FlowResult:
        """Handle a Haier AC discovered by this integration."""
        try:
            data, placeholders = _data_from_discovery_info(discovery_info)
        except (KeyError, ValueError):
            return self.async_abort(reason="invalid_discovery")

        await self.async_set_unique_id(data[CONF_MAC])
        self._abort_if_unique_id_configured(
            updates={
                CONF_HOST: data[CONF_HOST],
                CONF_PORT: data[CONF_PORT],
            }
        )
        self._discovery_data = data
        self._discovery_placeholders = placeholders
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm setup of a discovered Haier AC device."""
        if self._discovery_data is None:
            return self.async_abort(reason="invalid_discovery")

        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _test_connection(self._discovery_data)
            if not errors:
                return self.async_create_entry(
                    title=self._discovery_data[CONF_NAME],
                    data=self._discovery_data,
                )

        if hasattr(self, "_set_confirm_only"):
            self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders=self._discovery_placeholders,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguration of IP, port, and display settings."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            data, errors = _validate_user_input(user_input)
            if not errors:
                errors = await _test_connection(data)
            if not errors:
                await self.async_set_unique_id(data[CONF_MAC])
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=data,
                    reload_even_if_entry_is_unchanged=False,
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_data_schema(entry.data),
            errors=errors,
        )


async def _discover_defaults() -> dict[str, Any]:
    """Return config-flow defaults from UDP discovery, if a device responds."""
    try:
        devices = await async_discover_devices()
    except HaierACDiscoveryError as err:
        _LOGGER.debug("Could not run Haier AC UDP discovery: %s", err)
        return {}

    if not devices:
        return {}

    device = devices[0]
    name = getattr(device, "name", None)
    module_type = getattr(device, "module_type", None)
    firmware_version = getattr(device, "firmware_version", None)
    _LOGGER.info(
        "Discovered Haier AC at %s with MAC %s name=%s module=%s firmware=%s",
        device.host,
        device.mac,
        name,
        module_type,
        firmware_version,
    )
    defaults = {
        CONF_HOST: device.host,
        CONF_MAC: device.mac,
    }
    if device_info := _format_discovered_device_info(device):
        defaults[_DISCOVERED_DEVICE_INFO] = device_info
    return defaults


def _data_from_discovery_info(
    discovery_info: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return validated config data and placeholders from discovery info."""
    user_input = {
        CONF_NAME: discovery_info.get(CONF_NAME, DEFAULT_NAME),
        CONF_HOST: discovery_info[CONF_HOST],
        CONF_PORT: discovery_info.get(CONF_PORT, DEFAULT_PORT),
        CONF_MAC: discovery_info[CONF_MAC],
        CONF_TIMEOUT: discovery_info.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
    }
    data, errors = _validate_user_input(user_input)
    if errors:
        raise ValueError(errors)
    return data, _discovery_description_placeholders(data, discovery_info)


def _format_discovered_device_info(device: Any) -> str | None:
    """Return read-only device information shown in the config flow."""
    firmware_version = getattr(device, "firmware_version", None)
    module_type = getattr(device, "module_type", None)
    if firmware_version and module_type:
        return f"{firmware_version}{module_type}"
    return firmware_version or module_type


def _discovery_description_placeholders(
    data: dict[str, Any], discovery_info: dict[str, Any]
) -> dict[str, str]:
    """Return values displayed in the discovery confirmation step."""
    return {
        CONF_HOST: str(data[CONF_HOST]),
        CONF_MAC: str(data[CONF_MAC]),
        _DISCOVERED_DEVICE_INFO: str(
            discovery_info.get(
                _DISCOVERED_DEVICE_INFO, _DISCOVERED_DEVICE_INFO_NONE
            )
        ),
    }


async def _test_connection(data: dict[str, Any]) -> dict[str, str]:
    """Test the local connection before storing a config entry."""
    client = HaierACClient(
        host=data[CONF_HOST],
        port=data[CONF_PORT],
        mac=data[CONF_MAC],
        timeout=data[CONF_TIMEOUT],
        name=data[CONF_NAME],
    )
    try:
        await client.async_test_connection()
    except HaierACCommunicationError as err:
        _LOGGER.warning(
            "Could not connect to Haier AC at %s:%s with MAC %s: %s",
            data[CONF_HOST],
            data[CONF_PORT],
            data[CONF_MAC],
            err,
        )
        return {"base": "cannot_connect"}
    return {}


def _validate_user_input(
    user_input: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate user-entered config entry data."""
    errors: dict[str, str] = {}
    data = dict(user_input)

    try:
        data[CONF_HOST] = str(ip_address(str(user_input[CONF_HOST]).strip()))
    except ValueError:
        errors[CONF_HOST] = "invalid_ip"

    try:
        data[CONF_MAC] = normalize_mac(str(user_input[CONF_MAC]))
    except InvalidPacketError:
        errors[CONF_MAC] = "invalid_mac"

    data[CONF_NAME] = (str(user_input.get(CONF_NAME) or DEFAULT_NAME)).strip()
    data[CONF_PORT] = int(user_input.get(CONF_PORT, DEFAULT_PORT))
    try:
        timeout = int(str(user_input.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)).strip())
    except ValueError:
        errors[CONF_TIMEOUT] = "invalid_timeout"
    else:
        if 1 <= timeout <= 30:
            data[CONF_TIMEOUT] = timeout
        else:
            errors[CONF_TIMEOUT] = "invalid_timeout"
    return data, errors


def _data_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return the config entry form schema."""
    defaults = defaults or {}
    fields: dict[vol.Marker, Any] = {
        vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)): str,
    }

    if CONF_HOST in defaults:
        fields[vol.Required(CONF_HOST, default=defaults[CONF_HOST])] = str
    else:
        fields[vol.Required(CONF_HOST)] = str

    fields[
        vol.Optional(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT))
    ] = vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))

    if CONF_MAC in defaults:
        fields[vol.Required(CONF_MAC, default=defaults[CONF_MAC])] = str
    else:
        fields[vol.Required(CONF_MAC)] = str

    fields[
        vol.Optional(
            CONF_TIMEOUT, default=str(defaults.get(CONF_TIMEOUT, DEFAULT_TIMEOUT))
        )
    ] = str

    return vol.Schema(fields)


def _description_placeholders(defaults: dict[str, Any] | None = None) -> dict[str, str]:
    """Return read-only values displayed in the config flow description."""
    defaults = defaults or {}
    return {
        _DISCOVERED_DEVICE_INFO: str(
            defaults.get(_DISCOVERED_DEVICE_INFO, _DISCOVERED_DEVICE_INFO_NONE)
        )
    }
