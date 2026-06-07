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
from .const import CONF_MAC, CONF_TIMEOUT, DEFAULT_NAME, DEFAULT_PORT, DEFAULT_TIMEOUT, DOMAIN
from .protocol import InvalidPacketError, normalize_mac

_LOGGER = logging.getLogger(__name__)


class HaierACConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Haier AC Local."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

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

        return self.async_show_form(
            step_id="user",
            data_schema=_data_schema(),
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
    data[CONF_TIMEOUT] = int(user_input.get(CONF_TIMEOUT, DEFAULT_TIMEOUT))
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
        vol.Optional(CONF_TIMEOUT, default=defaults.get(CONF_TIMEOUT, DEFAULT_TIMEOUT))
    ] = vol.All(vol.Coerce(int), vol.Range(min=1, max=30))

    return vol.Schema(fields)
