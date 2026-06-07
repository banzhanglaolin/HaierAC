"""Tests for Haier AC UDP discovery helpers."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from custom_components.haier_ac.discovery import (
    DISCOVERY_ADDRESS,
    DISCOVERY_PORT,
    DISCOVERY_REQUEST,
    HaierACDiscoveredDevice,
    _DiscoveryProtocol,
    async_discover_devices,
    parse_discovery_response,
)


class DiscoveryProtocolTest(unittest.TestCase):
    """Exercise UDP discovery payload and response parsing."""

    def test_discovery_request_uses_haier_broadcast_payload(self) -> None:
        self.assertEqual(DISCOVERY_PORT, 7083)
        self.assertEqual(
            DISCOVERY_REQUEST,
            bytes(
                (
                    0x48,
                    0x61,
                    0x69,
                    0x65,
                    0x72,
                    0x00,
                    0x00,
                    0x69,
                    0x15,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x38,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x32,
                    0x2E,
                    0x30,
                    0x2E,
                    0x30,
                    0x00,
                    0x00,
                    0x00,
                    0x55,
                    0x44,
                    0x49,
                    0x53,
                    0x43,
                    0x4F,
                    0x56,
                    0x45,
                    0x52,
                    0x59,
                    0x5F,
                    0x53,
                    0x44,
                    0x4B,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                    0x00,
                )
            ),
        )

    def test_parse_discovery_response_uses_source_ip_and_plain_mac(self) -> None:
        device = parse_discovery_response(
            b"Haier\x000007A8B26279\x00UDISCOVERY_SDK",
            ("10.16.45.36", 7083),
        )

        self.assertEqual(
            device,
            HaierACDiscoveredDevice(host="10.16.45.36", mac="0007A8B26279"),
        )

    def test_parse_discovery_response_accepts_separated_mac(self) -> None:
        device = parse_discovery_response(
            b"mac=00:07:a8:b2:62:79",
            ("10.16.45.36", 7083),
        )

        self.assertEqual(
            device,
            HaierACDiscoveredDevice(host="10.16.45.36", mac="0007A8B26279"),
        )

    def test_parse_discovery_response_ignores_payload_without_mac(self) -> None:
        self.assertIsNone(
            parse_discovery_response(b"Haier\x00UDISCOVERY_SDK", ("10.16.45.36", 7083))
        )

    def test_discovery_protocol_deduplicates_by_mac(self) -> None:
        protocol = _DiscoveryProtocol()
        with self.assertLogs("custom_components.haier_ac.discovery", level="WARNING"):
            protocol.datagram_received(b"mac=00:07:a8:b2:62:79", ("10.16.45.36", 7083))
            protocol.datagram_received(b"mac=00:07:a8:b2:62:79", ("10.16.45.37", 7083))

        self.assertEqual(
            protocol.devices,
            [HaierACDiscoveredDevice(host="10.16.45.37", mac="0007A8B26279")],
        )

    def test_datagram_received_logs_raw_response(self) -> None:
        protocol = _DiscoveryProtocol()

        with self.assertLogs("custom_components.haier_ac.discovery", level="WARNING") as logs:
            protocol.datagram_received(
                b"Haier\x000007A8B26279\x00UDISCOVERY_SDK",
                ("10.16.45.36", 7083),
            )

        output = "\n".join(logs.output)
        self.assertIn("10.16.45.36:7083", output)
        self.assertIn("48 61 69 65 72", output)
        self.assertIn("Haier.0007A8B26279.UDISCOVERY_SDK", output)
        self.assertIn("host=10.16.45.36 mac=0007A8B26279", output)

    def test_datagram_received_logs_unparseable_response(self) -> None:
        protocol = _DiscoveryProtocol()

        with self.assertLogs("custom_components.haier_ac.discovery", level="WARNING") as logs:
            protocol.datagram_received(b"Haier\x00UDISCOVERY_SDK", ("10.16.45.36", 7083))

        output = "\n".join(logs.output)
        self.assertIn("did not contain a parseable MAC", output)


class DiscoveryBroadcastTest(unittest.IsolatedAsyncioTestCase):
    """Exercise the async UDP broadcast helper without touching the network."""

    async def test_async_discover_devices_sends_broadcast_request(self) -> None:
        sock = _Socket()
        transport = _Transport()
        loop = asyncio.get_running_loop()

        async def create_datagram_endpoint(factory, *, sock):
            protocol = factory()
            protocol.datagram_received(b"mac=00:07:a8:b2:62:79", ("10.16.45.36", 7083))
            return transport, protocol

        with patch(
            "custom_components.haier_ac.discovery._create_udp_socket",
            return_value=sock,
        ):
            with patch.object(
                loop, "create_datagram_endpoint", create_datagram_endpoint
            ):
                with self.assertLogs("custom_components.haier_ac.discovery", level="WARNING"):
                    devices = await async_discover_devices(timeout=0)

        self.assertEqual(
            transport.sent,
            [(DISCOVERY_REQUEST, (DISCOVERY_ADDRESS, DISCOVERY_PORT))],
        )
        self.assertTrue(transport.closed)
        self.assertEqual(
            devices,
            [HaierACDiscoveredDevice(host="10.16.45.36", mac="0007A8B26279")],
        )

    async def test_async_discover_devices_logs_broadcast_and_summary(self) -> None:
        sock = _Socket()
        transport = _Transport()
        loop = asyncio.get_running_loop()

        async def create_datagram_endpoint(factory, *, sock):
            protocol = factory()
            protocol.datagram_received(b"mac=00:07:a8:b2:62:79", ("10.16.45.36", 7083))
            return transport, protocol

        with patch(
            "custom_components.haier_ac.discovery._create_udp_socket",
            return_value=sock,
        ):
            with patch.object(
                loop, "create_datagram_endpoint", create_datagram_endpoint
            ):
                with self.assertLogs("custom_components.haier_ac.discovery", level="WARNING") as logs:
                    await async_discover_devices(timeout=0)

        output = "\n".join(logs.output)
        self.assertIn("broadcast to 255.255.255.255:7083", output)
        self.assertIn("48 61 69 65 72", output)
        self.assertIn("found 1 device(s): 10.16.45.36/0007A8B26279", output)


class _Socket:
    def __init__(self) -> None:
        self.closed = False

    def setblocking(self, flag: bool) -> None:
        return None

    def setsockopt(self, level: int, option: int, value: int) -> None:
        return None

    def bind(self, address: tuple[str, int]) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _Transport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def sendto(self, data: bytes, address: tuple[str, int]) -> None:
        self.sent.append((data, address))

    def close(self) -> None:
        self.closed = True


if __name__ == "__main__":
    unittest.main()