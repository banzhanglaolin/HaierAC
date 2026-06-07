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

    async def test_exchange_heartbeat_uses_local_ping_layout(self) -> None:
        client = HaierACClient(
            host="192.0.2.10",
            port=56800,
            mac="AABBCCDDEEFF",
            timeout=1,
            name="Haier AC",
        )
        response = _heartbeat_response(0, client.mac)
        reader = _Reader(response[:12], response[12:])
        writer = _Writer()

        await client._exchange_heartbeat(reader, writer)

        self.assertEqual(writer.data, build_heartbeat(0, client.mac))

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

        await client._exchange_heartbeat(reader, writer)

        self.assertEqual(writer.data, build_heartbeat(0, client.mac))


class _Reader:
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = list(chunks)

    async def readexactly(self, n: int) -> bytes:
        chunk = self._chunks.pop(0)
        if len(chunk) != n:
            raise AssertionError(f"expected read size {n}, got {len(chunk)}")
        return chunk


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
            b"\x00" * 4,
            struct.pack(">I", message_id),
            struct.pack(">I", 52),
            b"\x00" * 32,
            normalize_mac(mac).encode("ascii"),
            b"\x00" * 8,
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


if __name__ == "__main__":
    unittest.main()
