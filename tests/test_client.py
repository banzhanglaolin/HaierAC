"""Tests for the Haier AC local TCP client."""

from __future__ import annotations

import struct
import unittest
from unittest.mock import AsyncMock

from custom_components.haier_ac.client import HaierACClient, HaierACCommunicationError
from custom_components.haier_ac.protocol import (
    DataClass,
    InvalidPacketError,
    build_heartbeat,
    normalize_mac,
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
        reader = _Reader(response[:12], response[12:16], response[16:64], response[64:])
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
        reader = _Reader(response[:12], response[12:])
        writer = _Writer()

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING"):
            await client._exchange_heartbeat(reader, writer)

        self.assertEqual(writer.data, build_heartbeat(0, client.mac))

    async def test_exchange_heartbeat_accepts_empty_data_response(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        response = _empty_data_response()
        reader = _Reader(response)
        writer = _Writer()

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING"):
            await client._exchange_heartbeat(reader, writer)

        self.assertEqual(writer.data, build_heartbeat(0, client.mac))

    async def test_exchange_heartbeat_logs_request_and_response(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        response = _empty_data_response()
        reader = _Reader(response)
        writer = _Writer()

        with self.assertLogs("custom_components.haier_ac.client", level="WARNING") as logs:
            await client._exchange_heartbeat(reader, writer)

        output = "\n".join(logs.output)
        self.assertIn("heartbeat request to 192.0.2.10:56800", output)
        self.assertIn("message_id=0", output)
        self.assertIn("41 41 42 42 43 43 44 44 45 45 46 46", output)
        self.assertIn("heartbeat response from 192.0.2.10:56800", output)
        self.assertIn("DATA_RESPONSE(0x2715)", output)
        self.assertIn("00 00 27 15 00 00 00 00 00 00 00 00", output)


class _Reader:
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = list(chunks)

    async def readexactly(self, n: int) -> bytes:
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
            struct.pack(">I", 48),
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


def _empty_data_response() -> bytes:
    return b"".join(
        (
            b"\x00\x00",
            struct.pack(">H", DataClass.DATA_RESPONSE),
            b"\x00" * 8,
        )
    )


if __name__ == "__main__":
    unittest.main()
