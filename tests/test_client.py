"""Tests for the Haier AC local TCP client."""

from __future__ import annotations

import asyncio
import struct
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.haier_ac import client as client_module
from custom_components.haier_ac.client import HaierACClient, HaierACCommunicationError
from custom_components.haier_ac.protocol import (
    AC_AUX_HEAT_ON,
    AC_HEALTH_ON,
    ACStatus,
    DataClass,
    FanDirection,
    FanSpeed,
    InvalidPacketError,
    Mode,
    build_command,
    build_heartbeat,
    build_uart_short_command,
    normalize_mac,
    Subcommand,
)


class ClientConnectionTest(unittest.IsolatedAsyncioTestCase):
    """Exercise connection-test error handling used by the config flow."""

    async def test_async_test_connection_wraps_protocol_errors(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        writer = AsyncMock()
        client._open = AsyncMock(return_value=(AsyncMock(), writer))
        client._exchange_heartbeat = AsyncMock(
            side_effect=InvalidPacketError("bad heartbeat")
        )
        client._consume_startup_status_reports = AsyncMock()
        client._close = AsyncMock()

        with self.assertRaises(HaierACCommunicationError):
            await client.async_test_connection()

        client._close.assert_awaited_once_with(writer)

    async def test_exchange_heartbeat_uses_outer_heartbeat_layout(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        response = _heartbeat_response(0, client.mac)
        reader = _Reader(response[:4], response[4:16], response[16:])
        writer = _Writer()

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING"):
            await client._exchange_heartbeat(reader, writer)

        self.assertEqual(writer.data, build_heartbeat(0, client.mac))
        self.assertEqual(reader.remaining_chunks, 0)

    async def test_exchange_heartbeat_accepts_short_outer_response(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        response = _outer_heartbeat_response(0)
        reader = _Reader(response[:4], response[4:])
        writer = _Writer()

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING"):
            await client._exchange_heartbeat(reader, writer)

        self.assertEqual(writer.data, build_heartbeat(0, client.mac))

    async def test_exchange_heartbeat_rejects_non_heartbeat_response(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        response = _disconnect_response(0)
        reader = _Reader(response[:4], response[4:])
        writer = _Writer()

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING"):
            with self.assertRaises(InvalidPacketError):
                await client._exchange_heartbeat(reader, writer)

        self.assertEqual(writer.data, build_heartbeat(0, client.mac))

    async def test_consume_startup_status_reports_reads_active_reports(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=0.05,
            name="Haier AC",
        )
        response = _command_response(0, client.mac, _startup_status_uart())
        reader = _Reader(response[:4], response[4:80], response[80:])

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING") as logs:
            status = await client._consume_startup_status_reports(reader)

        output = "\n".join(logs.output)
        self.assertIn("startup status report from 192.0.2.10:56800", output)
        self.assertIn("startup status report UART from 192.0.2.10:56800", output)
        self.assertIn("DATA_RESPONSE(0x2715)", output)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.target_temperature, 26.0)
        self.assertEqual(client.status.target_temperature, 26.0)
        self.assertEqual(reader.remaining_chunks, 0)

    async def test_async_query_status_sends_command_between_heartbeats(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        startup_status = ACStatus(power_on=True, target_temperature=26.0)
        writer = _Writer()
        uart_frame = build_uart_short_command(Subcommand.QUERY_STATUS)
        command_response = _command_response(0, client.mac, uart_frame)
        client._open = AsyncMock(
            return_value=(
                _Reader(
                    command_response[:4],
                    command_response[4:80],
                    command_response[80:],
                ),
                writer,
            )
        )
        client._consume_startup_status_reports = AsyncMock(return_value=startup_status)
        client._exchange_heartbeat = AsyncMock(return_value=None)
        client._close = AsyncMock()

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING"):
            status = await client.async_query_status()

        self.assertEqual(status.target_temperature, 26.0)
        self.assertTrue(status.power_on)
        self.assertEqual(
            writer.data,
            build_command(0, client.mac, uart_frame),
        )
        self.assertEqual(client._exchange_heartbeat.await_count, 2)
        client._close.assert_not_awaited()

    async def test_exchange_heartbeat_consumes_status_reports_until_heartbeat(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        report = _command_response(0, client.mac, _startup_status_uart())
        heartbeat = _heartbeat_response(0, client.mac)
        reader = _Reader(
            report[:4],
            report[4:80],
            report[80:],
            heartbeat[:4],
            heartbeat[4:16],
            heartbeat[16:],
        )
        writer = _Writer()

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING") as logs:
            status = await client._exchange_heartbeat(reader, writer)

        output = "\n".join(logs.output)
        self.assertIn("status report before heartbeat response", output)
        self.assertIn("heartbeat response from 192.0.2.10:56800", output)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.target_temperature, 26.0)
        self.assertEqual(writer.data, build_heartbeat(0, client.mac))

    async def test_async_heartbeat_consumes_report_and_notifies_listener(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        report = _command_response(0, client.mac, _startup_status_uart())
        heartbeat = _heartbeat_response(0, client.mac)
        reader = _Reader(
            report[:4],
            report[4:80],
            report[80:],
            heartbeat[:4],
            heartbeat[4:16],
            heartbeat[16:],
        )
        writer = _Writer()
        updates: list[ACStatus] = []
        client.async_add_status_listener(updates.append)
        client._open = AsyncMock(return_value=(reader, writer))
        client._consume_startup_status_reports = AsyncMock(return_value=None)

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING"):
            status = await client.async_heartbeat()

        self.assertEqual(status.target_temperature, 26.0)
        self.assertEqual(client.status.target_temperature, 26.0)
        self.assertEqual(updates[-1].target_temperature, 26.0)
        self.assertEqual(writer.data, build_heartbeat(0, client.mac))

    async def test_async_heartbeat_notifies_listener_after_single_miss(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        client.status = ACStatus(power_on=True, target_temperature=26.0)
        updates: list[ACStatus] = []
        client.async_add_status_listener(updates.append)
        client._open = AsyncMock(return_value=(_Reader(), _Writer()))
        client._consume_startup_status_reports = AsyncMock(return_value=None)
        client._exchange_heartbeat = AsyncMock(side_effect=asyncio.TimeoutError)

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING"):
            status = await client.async_heartbeat()

        self.assertTrue(status.power_on)
        self.assertEqual(status.target_temperature, 26.0)
        self.assertEqual(updates[-1].target_temperature, 26.0)
        self.assertEqual(client._missed_heartbeats, 1)

    async def test_exchange_heartbeat_logs_request_and_response(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        response = _heartbeat_response(0, client.mac)
        reader = _Reader(response[:4], response[4:16], response[16:])
        writer = _Writer()

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING") as logs:
            await client._exchange_heartbeat(reader, writer)

        output = "\n".join(logs.output)
        self.assertIn("heartbeat request to 192.0.2.10:56800", output)
        self.assertIn("message_id=0", output)
        self.assertIn("41 41 42 42 43 43 44 44 45 45 46 46", output)
        self.assertIn("heartbeat response from 192.0.2.10:56800", output)
        self.assertIn("HEARTBEAT_RESPONSE(0x5DF3)", output)
        self.assertIn("00 00 5d f3", output)

    async def test_send_uart_logs_command_and_uart_packets(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        response = _command_response(0, client.mac, b"\xFF\xFF\x00")
        reader = _Reader(response[:4], response[4:80], response[80:])
        writer = _Writer()
        client._open = AsyncMock(return_value=(reader, writer))
        client._consume_startup_status_reports = AsyncMock()
        client._missed_heartbeats = 2
        client._exchange_heartbeat = AsyncMock(return_value=None)
        client._close = AsyncMock()

        with patch(
            "custom_components.haier_ac.client.parse_command_response",
            return_value=ACStatus(),
        ):
            with self.assertLogs("custom_components.haier_ac.client", level="WARNING") as logs:
                status = await client._send_uart(
                    build_uart_short_command(Subcommand.QUERY_STATUS)
                )

        self.assertIsInstance(status, ACStatus)
        output = "\n".join(logs.output)
        self.assertIn("command request to 192.0.2.10:56800", output)
        self.assertIn("command UART request to 192.0.2.10:56800", output)
        self.assertIn("command response from 192.0.2.10:56800", output)
        self.assertIn("command UART response from 192.0.2.10:56800", output)
        self.assertIn("DATA_REQUEST(0x2714)", output)
        self.assertIn("DATA_RESPONSE(0x2715)", output)
        self.assertEqual(client._exchange_heartbeat.await_count, 2)
        self.assertEqual(client._missed_heartbeats, 0)
        client._close.assert_not_awaited()

    async def test_send_uart_consumes_status_report_before_command_response(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        uart_frame = build_uart_short_command(Subcommand.QUERY_STATUS)
        first_heartbeat = _heartbeat_response(0, client.mac)
        report = _command_response(0, client.mac, _startup_status_uart())
        command_response = _command_response(1, client.mac, uart_frame)
        second_heartbeat = _heartbeat_response(2, client.mac)
        reader = _Reader(
            first_heartbeat[:4],
            first_heartbeat[4:16],
            first_heartbeat[16:],
            report[:4],
            report[4:80],
            report[80:],
            command_response[:4],
            command_response[4:80],
            command_response[80:],
            second_heartbeat[:4],
            second_heartbeat[4:16],
            second_heartbeat[16:],
        )
        writer = _Writer()
        client._open = AsyncMock(return_value=(reader, writer))
        client._consume_startup_status_reports = AsyncMock(return_value=None)

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING") as logs:
            status = await client._send_uart(uart_frame)

        output = "\n".join(logs.output)
        self.assertIn("status report before command response", output)
        self.assertEqual(status.target_temperature, 26.0)
        self.assertEqual(client.status.target_temperature, 26.0)

    async def test_async_query_status_fails_after_three_missed_heartbeats(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        client.status = ACStatus(power_on=True, target_temperature=26.0)
        client._open = AsyncMock(return_value=(_Reader(), _Writer()))
        client._consume_startup_status_reports = AsyncMock(return_value=None)
        client._exchange_heartbeat = AsyncMock(side_effect=asyncio.TimeoutError)

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING"):
            first = await client.async_query_status()
            second = await client.async_query_status()
            with self.assertRaises(HaierACCommunicationError):
                await client.async_query_status()

        self.assertTrue(first.power_on)
        self.assertTrue(second.power_on)
        self.assertEqual(first.target_temperature, 26.0)
        self.assertEqual(second.target_temperature, 26.0)
        self.assertEqual(client._missed_heartbeats, 3)
        self.assertEqual(client._exchange_heartbeat.await_count, 3)

    async def test_async_apply_clears_aux_heat_outside_heat_mode(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        client.status = ACStatus(
            power_on=True,
            mode=Mode.HEAT,
            fan_speed=FanSpeed.AUTO,
            fan_direction=FanDirection.OFF,
            target_temperature=24,
            aux_heat_on=True,
            health_on=True,
        )
        client._send_uart = AsyncMock(return_value=None)

        status = await client.async_apply(mode=Mode.COOL)

        frame = client._send_uart.await_args.args[0]
        power_options = struct.unpack_from(">H", frame, 28)[0]
        self.assertFalse(power_options & AC_AUX_HEAT_ON)
        self.assertTrue(power_options & AC_HEALTH_ON)
        self.assertEqual(status.mode, Mode.COOL)
        self.assertFalse(status.aux_heat_on)
        self.assertTrue(status.health_on)

    async def test_async_apply_sets_high_fan_when_switching_to_fan_only(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        client.status = ACStatus(
            power_on=True,
            mode=Mode.COOL,
            fan_speed=FanSpeed.AUTO,
            fan_direction=FanDirection.OFF,
            target_temperature=24,
        )
        client._send_uart = AsyncMock(return_value=None)

        status = await client.async_apply(mode=Mode.FAN)

        frame = client._send_uart.await_args.args[0]
        self.assertEqual(struct.unpack_from(">H", frame, 22)[0], Mode.FAN)
        self.assertEqual(struct.unpack_from(">H", frame, 24)[0], FanSpeed.HIGH)
        self.assertEqual(status.mode, Mode.FAN)
        self.assertEqual(status.fan_speed, FanSpeed.HIGH)

    async def test_send_uart_keeps_tcp_open_and_sends_post_command_heartbeat(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        uart_frame = build_uart_short_command(Subcommand.QUERY_STATUS)
        first_heartbeat = _heartbeat_response(0, client.mac)
        command_response = _command_response(1, client.mac, uart_frame)
        second_heartbeat = _heartbeat_response(2, client.mac)
        reader = _Reader(
            first_heartbeat[:4],
            first_heartbeat[4:16],
            first_heartbeat[16:],
            command_response[:4],
            command_response[4:80],
            command_response[80:],
            second_heartbeat[:4],
            second_heartbeat[4:16],
            second_heartbeat[16:],
        )
        writer = _Writer()
        client._open = AsyncMock(return_value=(reader, writer))
        client._consume_startup_status_reports = AsyncMock(return_value=None)

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING") as logs:
            await client._send_uart(uart_frame)

        output = "\n".join(logs.output)
        self.assertIn("heartbeat request to 192.0.2.10:56800", output)
        self.assertIn("command request to 192.0.2.10:56800", output)
        self.assertNotIn("DISCONNECT_REQUEST(0x65F6)", output)
        self.assertNotIn("00 00 65 f6", writer.data.hex(" "))
        self.assertEqual(
            writer.data,
            build_heartbeat(0, client.mac)
            + build_command(1, client.mac, uart_frame)
            + build_heartbeat(2, client.mac),
        )
        self.assertFalse(writer.closed)

    async def test_close_does_not_block_on_stuck_wait_closed(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        writer = _HangingCloseWriter()

        with patch.object(client_module, "_TCP_CLOSE_TIMEOUT", 0.01):
            await client._close(writer)

        self.assertTrue(writer.closed)


class _Reader:
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = list(chunks)

    async def readexactly(self, n: int) -> bytes:
        if not self._chunks:
            await asyncio.Future()
        chunk = self._chunks.pop(0)
        if len(chunk) != n:
            raise AssertionError(f"expected read size {n}, got {len(chunk)}")
        return chunk

    @property
    def remaining_chunks(self) -> int:
        return len(self._chunks)


class _Writer:
    def __init__(self) -> None:
        self.data = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed

    async def wait_closed(self) -> None:
        return None


class _HangingCloseWriter(_Writer):
    async def wait_closed(self) -> None:
        await asyncio.Future()


def _heartbeat_response(message_id: int, mac: str) -> bytes:
    return b"".join(
        (
            b"\x00\x00",
            struct.pack(">H", DataClass.HEARTBEAT_RESPONSE),
            b"\x00" * 4,
            struct.pack(">I", message_id),
            struct.pack(">I", 52),
            b"\x00" * 32,
            normalize_mac(mac).encode("ascii"),
            b"\x00" * 4,
            b"\x00" * 4,
        )
    )


def _outer_heartbeat_response(message_id: int) -> bytes:
    return b"".join(
        (
            b"\x00\x00",
            struct.pack(">H", DataClass.HEARTBEAT_RESPONSE),
            b"\x00" * 4,
            struct.pack(">I", message_id),
            b"\x00" * 4,
        )
    )


def _command_response(message_id: int, mac: str, uart_frame: bytes) -> bytes:
    return b"".join(
        (
            b"\x00\x00",
            struct.pack(">H", DataClass.DATA_RESPONSE),
            b"\x00" * 36,
            normalize_mac(mac).encode("ascii"),
            b"\x00" * 20,
            struct.pack(">I", message_id),
            struct.pack(">I", len(uart_frame)),
            uart_frame,
        )
    )


def _disconnect_response(message_id: int) -> bytes:
    return b"".join(
        (
            b"\x00\x00",
            struct.pack(">H", DataClass.DISCONNECT_RESPONSE),
            b"\x00" * 4,
            struct.pack(">I", message_id),
            b"\x00" * 4,
        )
    )


def _startup_status_uart() -> bytes:
    return bytes.fromhex(
        "ff ff 22 00 00 00 00 00 01 06 6d 01 00 1b 00 33 00 00 00 "
        "00 00 00 00 01 00 00 00 00 00 00 00 00 00 00 00 0a f0"
    )


if __name__ == "__main__":
    unittest.main()
