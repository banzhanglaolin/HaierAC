"""Tests for the Haier AC local TCP client."""

from __future__ import annotations

import asyncio
import struct
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.haier_ac.client import HaierACClient, HaierACCommunicationError
from custom_components.haier_ac.protocol import (
    ACStatus,
    DataClass,
    InvalidPacketError,
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
            await client._consume_startup_status_reports(reader)

        output = "\n".join(logs.output)
        self.assertIn("startup status report from 192.0.2.10:56800", output)
        self.assertIn("startup status report UART from 192.0.2.10:56800", output)
        self.assertIn("DATA_RESPONSE(0x2715)", output)
        self.assertEqual(client.status.target_temperature, 26.0)
        self.assertEqual(reader.remaining_chunks, 0)

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
            await client._exchange_heartbeat(reader, writer)

        output = "\n".join(logs.output)
        self.assertIn("status report before heartbeat response", output)
        self.assertIn("heartbeat response from 192.0.2.10:56800", output)
        self.assertEqual(writer.data, build_heartbeat(0, client.mac))

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
        response = _command_response(1, client.mac, b"\xFF\xFF\x00")
        reader = _Reader(response[:80], response[80:])
        writer = _Writer()
        client._open = AsyncMock(return_value=(reader, writer))
        client._consume_startup_status_reports = AsyncMock()
        client._exchange_heartbeat = AsyncMock()
        client._exchange_disconnect = AsyncMock()
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

    async def test_exchange_disconnect_logs_request_and_response(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        response = _disconnect_response(0)
        reader = _Reader(response)
        writer = _Writer()

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING") as logs:
            await client._exchange_disconnect(reader, writer)

        output = "\n".join(logs.output)
        self.assertIn("disconnect request to 192.0.2.10:56800", output)
        self.assertIn("disconnect response from 192.0.2.10:56800", output)
        self.assertIn("DISCONNECT_REQUEST(0x65F6)", output)
        self.assertIn("DISCONNECT_RESPONSE(0x65F7)", output)


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

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        return None


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
