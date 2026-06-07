"""Async TCP client for Haier AC local control."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
import logging

from .protocol import (
    ACStatus,
    DataClass,
    FanDirection,
    FanSpeed,
    HaierProtocolError,
    InvalidPacketError,
    Mode,
    Subcommand,
    build_command,
    build_disconnect,
    build_heartbeat,
    build_uart_set_state,
    build_uart_short_command,
    normalize_mac,
    parse_command_response,
    parse_disconnect_response,
    parse_heartbeat_response,
)

_LOGGER = logging.getLogger(__name__)


class HaierACCommunicationError(Exception):
    """Raised when communication with the air conditioner fails."""


class HaierACClient:
    """Small TCP client that speaks the local Haier AC payload protocol."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        mac: str,
        timeout: int,
        name: str,
    ) -> None:
        self.host = host
        self.port = port
        self.mac = normalize_mac(mac)
        self.timeout = timeout
        self.name = name
        self.status = ACStatus()
        self._message_id = 0
        self._lock = asyncio.Lock()

    async def async_test_connection(self) -> None:
        """Open a socket and perform the heartbeat handshake."""
        async with self._lock:
            try:
                reader, writer = await self._open()
                try:
                    await self._exchange_heartbeat(reader, writer)
                finally:
                    await self._close(writer)
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as err:
                raise HaierACCommunicationError(
                    f"Failed to communicate with {self.host}:{self.port}"
                ) from err
            except HaierProtocolError as err:
                raise HaierACCommunicationError(str(err)) from err

    async def async_query_status(self) -> ACStatus:
        """Query the current AC status."""
        status = await self._send_uart(build_uart_short_command(Subcommand.QUERY_STATUS))
        if status is not None:
            self.status = status
        return self.status

    async def async_turn_on(self) -> ACStatus:
        """Turn the AC on."""
        status = await self._send_uart(build_uart_short_command(Subcommand.TURN_ON))
        self.status = status or replace(self.status, power_on=True)
        return self.status

    async def async_turn_off(self) -> ACStatus:
        """Turn the AC off."""
        status = await self._send_uart(build_uart_short_command(Subcommand.TURN_OFF))
        self.status = status or replace(self.status, power_on=False)
        return self.status

    async def async_apply(
        self,
        *,
        mode: Mode | None = None,
        fan_speed: FanSpeed | None = None,
        fan_direction: FanDirection | None = None,
        target_temperature: float | None = None,
        power_on: bool = True,
    ) -> ACStatus:
        """Apply a full AC state using the long UART command."""
        desired = replace(
            self.status,
            power_on=power_on,
            mode=mode if mode is not None else self.status.mode,
            fan_speed=fan_speed if fan_speed is not None else self.status.fan_speed,
            fan_direction=fan_direction
            if fan_direction is not None
            else self.status.fan_direction,
            target_temperature=target_temperature
            if target_temperature is not None
            else self.status.target_temperature,
        )
        frame = build_uart_set_state(
            mode=desired.mode,
            fan_speed=desired.fan_speed,
            fan_direction=desired.fan_direction,
            power_on=desired.power_on,
            target_temperature=desired.target_temperature,
            current_temperature=desired.current_temperature,
            current_humidity=desired.current_humidity,
        )
        status = await self._send_uart(frame)
        self.status = status or desired
        return self.status

    async def _send_uart(self, uart_frame: bytes) -> ACStatus | None:
        async with self._lock:
            reader, writer = await self._open()
            try:
                await self._exchange_heartbeat(reader, writer)
                message_id = self._next_message_id()
                writer.write(build_command(message_id, self.mac, uart_frame))
                await self._drain(writer)

                prefix = await self._read_exactly(reader, 80)
                uart_len = int.from_bytes(prefix[76:80], "big")
                uart_payload = await self._read_exactly(reader, uart_len)
                status = parse_command_response(
                    prefix + uart_payload, message_id, self.mac
                )

                with suppress(Exception):
                    await self._exchange_disconnect(reader, writer)
                return status
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as err:
                raise HaierACCommunicationError(
                    f"Failed to communicate with {self.host}:{self.port}"
                ) from err
            except HaierProtocolError as err:
                raise HaierACCommunicationError(str(err)) from err
            finally:
                await self._close(writer)

    async def _open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.timeout
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise HaierACCommunicationError(
                f"Could not connect to {self.host}:{self.port}"
            ) from err

    async def _exchange_heartbeat(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        message_id = self._next_message_id()
        request = build_heartbeat(message_id, self.mac)
        _LOGGER.warning(
            "Haier AC heartbeat request to %s:%s: message_id=%s mac=%s "
            "length=%s hex=%s ascii=%r",
            self.host,
            self.port,
            message_id,
            self.mac,
            len(request),
            request.hex(" "),
            _format_ascii(request),
        )
        writer.write(request)
        await self._drain(writer)

        header = await self._read_exactly(reader, 12)
        if int.from_bytes(header[2:4], "big") == DataClass.HEARTBEAT_RESPONSE:
            length_bytes = await self._read_exactly(reader, 4)
            payload_len = int.from_bytes(length_bytes, "big")
            payload = (
                b""
                if payload_len == 0
                else await self._read_exactly(reader, payload_len)
            )
            response = header + length_bytes + payload
        else:
            payload_len = int.from_bytes(header[8:12], "big")
            payload = (
                b""
                if payload_len == 0
                else await self._read_exactly(reader, payload_len)
            )
            response = header + payload

        _LOGGER.warning(
            "Haier AC heartbeat response from %s:%s: request_message_id=%s "
            "type=%s u32_at_4=%s u32_at_8=%s length=%s hex=%s ascii=%r",
            self.host,
            self.port,
            message_id,
            _format_data_class(response),
            int.from_bytes(response[4:8], "big") if len(response) >= 8 else None,
            int.from_bytes(response[8:12], "big") if len(response) >= 12 else None,
            len(response),
            response.hex(" "),
            _format_ascii(response),
        )

        try:
            parse_heartbeat_response(response, message_id, self.mac)
        except InvalidPacketError as err:
            _LOGGER.warning(
                "Invalid heartbeat response from %s:%s: %s; hex=%s ascii=%r",
                self.host,
                self.port,
                err,
                response.hex(" "),
                _format_ascii(response),
            )
            raise

    async def _exchange_disconnect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        message_id = self._next_message_id()
        writer.write(build_disconnect(message_id))
        await self._drain(writer)
        response = await self._read_exactly(reader, 16)
        try:
            parse_disconnect_response(response, message_id)
        except InvalidPacketError:
            _LOGGER.debug("Ignoring invalid disconnect response", exc_info=True)

    async def _read_exactly(
        self, reader: asyncio.StreamReader, n: int
    ) -> bytes:
        return await asyncio.wait_for(reader.readexactly(n), timeout=self.timeout)

    async def _drain(self, writer: asyncio.StreamWriter) -> None:
        await asyncio.wait_for(writer.drain(), timeout=self.timeout)

    async def _close(self, writer: asyncio.StreamWriter) -> None:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()

    def _next_message_id(self) -> int:
        message_id = self._message_id
        self._message_id = (self._message_id + 1) & 0xFFFFFFFF
        return message_id


def _format_ascii(data: bytes) -> str:
    return "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in data)


def _format_data_class(data: bytes) -> str:
    if len(data) < 4:
        return "unknown"
    value = int.from_bytes(data[2:4], "big")
    try:
        data_class = DataClass(value)
    except ValueError:
        return f"0x{value:04X}"
    return f"{data_class.name}(0x{value:04X})"
