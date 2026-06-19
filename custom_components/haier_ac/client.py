"""Async TCP client for Haier AC local control."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
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
    build_heartbeat,
    build_uart_set_state,
    build_uart_short_command,
    normalize_mac,
    parse_command_response,
    parse_heartbeat_response,
)

_LOGGER = logging.getLogger(__name__)

_STARTUP_REPORT_FIRST_TIMEOUT = 2.0
_STARTUP_REPORT_IDLE_TIMEOUT = 0.25
_STARTUP_REPORT_LIMIT = 5
_MAX_MISSED_HEARTBEATS = 3
_TCP_CLOSE_TIMEOUT = 1.0
StatusListener = Callable[[ACStatus], None]


class HaierACCommunicationError(Exception):
    """Raised when communication with the air conditioner fails."""


class _HeartbeatMissed(Exception):
    """Raised when a heartbeat is missed but the device is not failed yet."""


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
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._missed_heartbeats = 0
        self._status_listeners: set[StatusListener] = set()

    def async_add_status_listener(
        self, listener: StatusListener
    ) -> Callable[[], None]:
        """Register a callback for status reports received from the device."""
        self._status_listeners.add(listener)

        def remove_listener() -> None:
            self._status_listeners.discard(listener)

        return remove_listener

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
            self._set_status(status)
        return self.status

    async def async_close(self) -> None:
        """Close the cached TCP connection."""
        async with self._lock:
            await self._close_connection()

    async def async_heartbeat(self) -> ACStatus:
        """Keep the TCP connection alive and consume queued status reports."""
        async with self._lock:
            try:
                reader, writer, startup_status = await self._ensure_connection()
                if startup_status is not None:
                    self._set_status(startup_status)
                try:
                    heartbeat_status = await self._exchange_heartbeat_with_retry(
                        reader, writer
                    )
                except _HeartbeatMissed:
                    self._set_status(self.status)
                    return self.status
                if heartbeat_status is not None:
                    self._set_status(heartbeat_status)
                return self.status
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as err:
                await self._close_connection()
                raise HaierACCommunicationError(
                    f"Failed to communicate with {self.host}:{self.port}"
                ) from err
            except HaierProtocolError as err:
                await self._close_connection()
                raise HaierACCommunicationError(str(err)) from err

    async def async_turn_on(self) -> ACStatus:
        """Turn the AC on."""
        status = await self._send_uart(build_uart_short_command(Subcommand.TURN_ON))
        self._set_status(status or replace(self.status, power_on=True))
        return self.status

    async def async_turn_off(self) -> ACStatus:
        """Turn the AC off."""
        status = await self._send_uart(build_uart_short_command(Subcommand.TURN_OFF))
        self._set_status(status or replace(self.status, power_on=False))
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
        if desired.aux_heat_on and (not desired.power_on or desired.mode != Mode.HEAT):
            desired = replace(desired, aux_heat_on=False)
        if (
            desired.power_on
            and desired.mode == Mode.FAN
            and desired.fan_speed == FanSpeed.AUTO
        ):
            desired = replace(desired, fan_speed=FanSpeed.HIGH)
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
        self._set_status(status or desired)
        return self.status

    async def _send_uart(
        self, uart_frame: bytes, *, use_startup_status: bool = False
    ) -> ACStatus | None:
        async with self._lock:
            try:
                reader, writer, startup_status = await self._ensure_connection()
                try:
                    heartbeat_status = await self._exchange_heartbeat_with_retry(
                        reader, writer
                    )
                except _HeartbeatMissed:
                    self._set_status(self.status)
                    return self.status
                pre_command_status = heartbeat_status or startup_status
                if pre_command_status is not None:
                    self._set_status(pre_command_status)

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

                command_status = await self._read_command_response(reader, message_id)

                try:
                    post_heartbeat_status = await self._exchange_heartbeat_with_retry(
                        reader, writer
                    )
                except _HeartbeatMissed:
                    if command_status is not None:
                        self._set_status(command_status)
                    elif pre_command_status is not None:
                        self._set_status(pre_command_status)
                    else:
                        self._set_status(self.status)
                    return command_status or pre_command_status
                return post_heartbeat_status or command_status or pre_command_status
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as err:
                await self._close_connection()
                raise HaierACCommunicationError(
                    f"Failed to communicate with {self.host}:{self.port}"
                ) from err
            except HaierProtocolError as err:
                await self._close_connection()
                raise HaierACCommunicationError(str(err)) from err

    async def _open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.timeout
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise HaierACCommunicationError(
                f"Could not connect to {self.host}:{self.port}"
            ) from err

    async def _ensure_connection(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, ACStatus | None]:
        """Return the cached TCP connection, opening it if needed."""
        if (
            self._reader is not None
            and self._writer is not None
            and not self._writer_is_closing(self._writer)
        ):
            return self._reader, self._writer, None

        if self._writer is not None:
            await self._close_connection()

        reader, writer = await self._open()
        self._reader = reader
        self._writer = writer
        startup_status = await self._consume_startup_status_reports(reader)
        return reader, writer, startup_status

    async def _exchange_heartbeat_with_retry(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> ACStatus | None:
        """Exchange one heartbeat and tolerate a few consecutive no-responses."""
        try:
            status = await self._exchange_heartbeat(reader, writer)
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as err:
            await self._close_connection()
            self._missed_heartbeats += 1
            _LOGGER.warning(
                "Haier AC heartbeat missed from %s:%s (%s/%s)",
                self.host,
                self.port,
                self._missed_heartbeats,
                _MAX_MISSED_HEARTBEATS,
            )
            if self._missed_heartbeats < _MAX_MISSED_HEARTBEATS:
                raise _HeartbeatMissed from err
            raise
        else:
            self._missed_heartbeats = 0
            return status

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

    async def _read_command_response(
        self, reader: asyncio.StreamReader, message_id: int
    ) -> ACStatus | None:
        deadline = asyncio.get_running_loop().time() + self.timeout
        latest_status: ACStatus | None = None
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            response = await self._read_tcp_packet(
                reader, first_byte_timeout=remaining
            )
            if _data_class(response) != DataClass.DATA_RESPONSE:
                _log_tcp_packet(
                    self.host,
                    self.port,
                    "packet before command response",
                    "from",
                    response,
                    message_id=message_id,
                )
                raise InvalidPacketError("unexpected command packet type")

            response_message_id = int.from_bytes(response[72:76], "big")
            uart_len = int.from_bytes(response[76:80], "big")
            if response_message_id != message_id:
                status = self._handle_status_report(
                    response, "status report before command response"
                )
                if status is not None:
                    latest_status = status
                continue

            _log_tcp_packet(
                self.host,
                self.port,
                "command response",
                "from",
                response,
                message_id=message_id,
                uart_length=uart_len,
            )
            uart_payload = response[80:]
            if uart_payload:
                _log_tcp_packet(
                    self.host,
                    self.port,
                    "command UART response",
                    "from",
                    uart_payload,
                    message_id=message_id,
                )
            return (
                parse_command_response(response, message_id, self.mac)
                or latest_status
            )

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
            self._set_status(status)
        return status

    def _set_status(self, status: ACStatus) -> None:
        self.status = status
        for listener in tuple(self._status_listeners):
            try:
                listener(status)
            except Exception:
                _LOGGER.exception("Haier AC status listener failed")

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
            await asyncio.wait_for(
                writer.wait_closed(), timeout=_TCP_CLOSE_TIMEOUT
            )

    async def _close_connection(self) -> None:
        """Close and forget the cached TCP connection."""
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is not None:
            await self._close(writer)

    @staticmethod
    def _writer_is_closing(writer: asyncio.StreamWriter) -> bool:
        """Return whether a writer is already closing."""
        is_closing = getattr(writer, "is_closing", None)
        return bool(is_closing()) if callable(is_closing) else False

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
