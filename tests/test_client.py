"""Tests for the Haier AC local TCP client."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from custom_components.haier_ac.client import HaierACClient, HaierACCommunicationError
from custom_components.haier_ac.protocol import InvalidPacketError


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


if __name__ == "__main__":
    unittest.main()
