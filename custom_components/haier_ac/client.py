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

_STARTUP_REPORT_FIRST_TIMEOUT = 2.0
_STARTUP_REPORT_IDLE_TIMEOUT = 0.25
_STARTUP_REPORT_LIMIT = 5


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
                    await self._consume_startup_status_reports(reader)
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
        status = await self._send_uart(
            build_uart_short_command(Subcommand.QUERY_STATUS),
            use_startup_status=True,
        )
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
            aux_heat_on=desired.aux_heat_on,
            health_on=desired.health_on,
        )
        status = await self._send_uart(frame)
        self.status = status or desired
        return self.status

    async def _send_uart(
        self, uart_frame: bytes, *, use_startup_status: bool = False
    ) -> ACStatus | None:
        async with self._lock:
            reader, writer = await self._open()
            try:
                startup_status = await self._consume_startup_status_reports(reader)
                heartbeat_status = await self._exchange_heartbeat(reader, writer)
                status = heartbeat_status or startup_status
                if use_startup_status and status is not None:
                    self.status = status
                    with suppress(Exception):
                        await self._exchange_disconnect(reader, writer)
                    return status

                message_id = self._next_message_id()
                request = build_command(message_id, self.mac, uart_frame)
                _log_tcp_packet(
                    self.host,
                    self.port,
                    "command request",
                    "to",
                    request,
                    message_id=message_id,
                    uart_length=len(uart_frame),
                )
                _log_tcp_packet(
                    self.host,
                    self.port,
                    "command UART request",
                    "to",
                    uart_frame,
                    message_id=message_id,
                )
                writer.write(request)
                await self._drain(writer)

                prefix = await self._read_exactly(reader, 80)
                uart_len = int.from_bytes(prefix[76:80], "big")
                uart_payload = await self._read_exactly(reader, uart_len)
                response = prefix + uart_payload
                _log_tcp_packet(
                    self.host,
                    self.port,
                    "command response",
                    "from",
                    response,
                    message_id=message_id,
                    uart_length=uart_len,
                )
                if uart_payload:
                    _log_tcp_packet(
                        self.host,
                        self.port,
                        "command UART response",
                        "from",
                        uart_payload,
                        message_id=message_id,
                    )
                status = parse_command_response(
                    response, message_id, self.mac
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
    ) -> ACStatus | None:
        message_id = self._next_message_id()
        request = build_heartbeat(message_id, self.mac)
        _log_tcp_packet(
            self.host,
            self.port,
            "heartbeat request",
            "to",
            request,
            message_id=message_id,
            mac=self.mac,
        )
        writer.write(request)
        await self._drain(writer)

        deadline = asyncio.get_running_loop().time() + self.timeout
        latest_status: ACStatus | None = None
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            response = await self._read_tcp_packet(
                reader, first_byte_timeout=remaining
            )
            if _data_class(response) == DataClass.DATA_RESPONSE:
                status = self._handle_status_report(
                    response, "status report before heartbeat response"
                )
                if status is not None:
                    latest_status = status
                continue
            break

        _log_tcp_packet(
            self.host,
            self.port,
            "heartbeat response",
            "from",
            response,
            request_message_id=message_id,
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
        return latest_status

    async def _consume_startup_status_reports(
        self, reader: asyncio.StreamReader
    ) -> ACStatus | None:
        """Read status reports the device sends immediately after TCP connect."""
        latest_status: ACStatus | None = None
        for index in range(_STARTUP_REPORT_LIMIT):
            timeout = (
                min(float(self.timeout), _STARTUP_REPORT_FIRST_TIMEOUT)
                if index == 0
                else min(float(self.timeout), _STARTUP_REPORT_IDLE_TIMEOUT)
            )
            try:
                response = await self._read_tcp_packet(
                    reader, first_byte_timeout=timeout
                )
            except asyncio.TimeoutError:
                return latest_status

            if _data_class(response) != DataClass.DATA_RESPONSE:
                _log_tcp_packet(
                    self.host,
                    self.port,
                    "startup packet",
                    "from",
                    response,
                )
                raise InvalidPacketError("unexpected startup packet type")

            status = self._handle_status_report(response, "startup status report")
            if status is not None:
                latest_status = status
        return latest_status

    def _handle_status_report(self, response: bytes, label: str) -> ACStatus | None:
        report_message_id = int.from_bytes(response[72:76], "big")
        uart_len = int.from_bytes(response[76:80], "big")
        _log_tcp_packet(
            self.host,
            self.port,
            label,
            "from",
            response,
            report_message_id=report_message_id,
            uart_length=uart_len,
        )
        uart_frame = response[80:]
        if uart_frame:
            _log_tcp_packet(
                self.host,
                self.port,
                f"{label} UART",
                "from",
                uart_frame,
                report_message_id=report_message_id,
            )

        try:
            status = parse_command_response(response, report_message_id, self.mac)
        except InvalidPacketError as err:
            _LOGGER.warning(
                "Invalid Haier AC %s from %s:%s: %s; hex=%s ascii=%r",
                label,
                self.host,
                self.port,
                err,
                response.hex(" "),
                _format_ascii(response),
            )
            return None
        if status is not None:
            self.status = status
        return status

    async def _exchange_disconnect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        message_id = self._next_message_id()
        request = build_disconnect(message_id)
        _log_tcp_packet(
            self.host,
            self.port,
            "disconnect request",
            "to",
            request,
            message_id=message_id,
        )
        writer.write(request)
        await self._drain(writer)
        response = await self._read_exactly(reader, 16)
        _log_tcp_packet(
            self.host,
            self.port,
            "disconnect response",
            "from",
            response,
            request_message_id=message_id,
        )
        try:
            parse_disconnect_response(response, message_id)
        except InvalidPacketError:
            _LOGGER.debug("Ignoring invalid disconnect response", exc_info=True)

    async def _read_tcp_packet(
        self,
        reader: asyncio.StreamReader,
        *,
        first_byte_timeout: float | None = None,
    ) -> bytes:
        header = await self._read_exactly(reader, 4, timeout=first_byte_timeout)
        packet_type = _data_class(header)
        if packet_type == DataClass.DATA_RESPONSE:
            prefix = await self._read_exactly(reader, 76)
            uart_len = int.from_bytes(prefix[72:76], "big")
            uart_payload = (
                b"" if uart_len == 0 else await self._read_exactly(reader, uart_len)
            )
            return header + prefix + uart_payload
        if packet_type == DataClass.HEARTBEAT_RESPONSE:
            prefix = await self._read_exactly(reader, 12)
            payload_len = int.from_bytes(prefix[8:12], "big")
            payload = await self._read_heartbeat_response_payload(
                reader, payload_len
            )
            return header + prefix + payload
        if packet_type in {
            DataClass.DISCONNECT_RESPONSE,
            DataClass.DISCONNECT_REQUEST,
            DataClass.HEARTBEAT_REQUEST,
        }:
            return header + await self._read_exactly(reader, 12)
        return header + await self._read_exactly(reader, 8)

    async def _read_exactly(
        self,
        reader: asyncio.StreamReader,
        n: int,
        *,
        timeout: float | None = None,
    ) -> bytes:
        return await asyncio.wait_for(
            reader.readexactly(n), timeout=self.timeout if timeout is None else timeout
        )

    async def _read_heartbeat_response_payload(
        self, reader: asyncio.StreamReader, payload_len: int
    ) -> bytes:
        if payload_len == 0:
            return b""
        return await self._read_exactly(reader, payload_len)

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


def _data_class(data: bytes) -> DataClass | int | None:
    if len(data) < 4:
        return None
    value = int.from_bytes(data[2:4], "big")
    try:
        return DataClass(value)
    except ValueError:
        return value


def _log_tcp_packet(
    host: str,
    port: int,
    label: str,
    direction: str,
    data: bytes,
    **fields: object,
) -> None:
    details = {
        **fields,
        "type": _format_data_class(data),
        "u32_at_4": int.from_bytes(data[4:8], "big") if len(data) >= 8 else None,
        "u32_at_8": int.from_bytes(data[8:12], "big") if len(data) >= 12 else None,
        "u32_at_12": int.from_bytes(data[12:16], "big") if len(data) >= 16 else None,
        "length": len(data),
    }
    detail_text = " ".join(f"{key}={value}" for key, value in details.items())
    _LOGGER.warning(
        "Haier AC %s %s %s:%s: %s hex=%s ascii=%r",
        label,
        direction,
        host,
        port,
        detail_text,
        data.hex(" "),
        _format_ascii(data),
    )


def _format_data_class(data: bytes) -> str:
    if len(data) < 4:
        return "unknown"
    value = int.from_bytes(data[2:4], "big")
    try:
        data_class = DataClass(value)
    except ValueError:
        return f"0x{value:04X}"
    return f"{data_class.name}(0x{value:04X})"
