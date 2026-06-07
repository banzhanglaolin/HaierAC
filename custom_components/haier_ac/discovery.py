"""UDP discovery helpers for Haier AC devices."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
import logging
import re
import socket

from .protocol import InvalidPacketError, normalize_mac

_LOGGER = logging.getLogger(__name__)

DISCOVERY_ADDRESS = "255.255.255.255"
DISCOVERY_PORT = 7083
DISCOVERY_TIMEOUT = 3.0
DISCOVERY_REQUEST = bytes(
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
)

_MAC_WITH_SEPARATORS = re.compile(
    r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f])"
)
_MAC_PLAIN = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{12}(?![0-9a-f])")


@dataclass(frozen=True, slots=True)
class HaierACDiscoveredDevice:
    """A Haier AC device discovered by UDP broadcast."""

    host: str
    mac: str


class HaierACDiscoveryError(Exception):
    """Raised when UDP discovery cannot be started."""


def parse_discovery_response(
    data: bytes, addr: tuple[str, int] | Sequence[object]
) -> HaierACDiscoveredDevice | None:
    """Extract a device IP and MAC from a UDP discovery response."""
    if not addr:
        return None

    host = str(addr[0])
    mac = _extract_mac(data)
    if mac is None:
        return None
    return HaierACDiscoveredDevice(host=host, mac=mac)


async def async_discover_devices(
    *,
    timeout: float = DISCOVERY_TIMEOUT,
    address: str = DISCOVERY_ADDRESS,
    port: int = DISCOVERY_PORT,
) -> list[HaierACDiscoveredDevice]:
    """Broadcast a UDP discovery request and collect responding devices."""
    loop = asyncio.get_running_loop()
    protocol = _DiscoveryProtocol()
    sock = _create_udp_socket()
    try:
        sock.setblocking(False)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 0))
        transport, _ = await loop.create_datagram_endpoint(lambda: protocol, sock=sock)
    except OSError as err:
        sock.close()
        raise HaierACDiscoveryError("Could not start UDP discovery") from err

    try:
        _LOGGER.warning(
            "Haier AC UDP discovery broadcast to %s:%s (%s bytes): %s",
            address,
            port,
            len(DISCOVERY_REQUEST),
            DISCOVERY_REQUEST.hex(" "),
        )
        transport.sendto(DISCOVERY_REQUEST, (address, port))
        await asyncio.sleep(timeout)
    finally:
        transport.close()

    if protocol.devices:
        _LOGGER.warning(
            "Haier AC UDP discovery found %s device(s): %s",
            len(protocol.devices),
            ", ".join(f"{device.host}/{device.mac}" for device in protocol.devices),
        )
    else:
        _LOGGER.warning("Haier AC UDP discovery received no usable device responses")

    return protocol.devices


def _create_udp_socket() -> socket.socket:
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _format_ascii(data: bytes) -> str:
    return "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in data)


def _extract_mac(data: bytes) -> str | None:
    text = data.decode("ascii", errors="ignore")
    for pattern in (_MAC_WITH_SEPARATORS, _MAC_PLAIN):
        for match in pattern.finditer(text):
            candidate = match.group(0)
            try:
                mac = normalize_mac(candidate)
            except InvalidPacketError:
                continue
            if mac not in {"000000000000", "FFFFFFFFFFFF"}:
                return mac
    return None


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self._devices: dict[str, HaierACDiscoveredDevice] = {}

    @property
    def devices(self) -> list[HaierACDiscoveredDevice]:
        return list(self._devices.values())

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        _LOGGER.warning(
            "Haier AC UDP discovery response from %s:%s (%s bytes): hex=%s ascii=%r",
            addr[0],
            addr[1],
            len(data),
            data.hex(" "),
            _format_ascii(data),
        )
        device = parse_discovery_response(data, addr)
        if device is not None:
            _LOGGER.warning(
                "Haier AC UDP discovery parsed device: host=%s mac=%s",
                device.host,
                device.mac,
            )
            self._devices[device.mac] = device
        else:
            _LOGGER.warning(
                "Haier AC UDP discovery response from %s:%s did not contain a parseable MAC",
                addr[0],
                addr[1],
            )

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("Ignoring UDP discovery error: %s", exc)