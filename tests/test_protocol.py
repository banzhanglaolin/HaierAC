"""Tests for Haier AC local protocol helpers."""

from __future__ import annotations

import struct
import unittest

from custom_components.haier_ac.protocol import (
    AC_STATE_ON,
    DataClass,
    FanDirection,
    FanSpeed,
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
    parse_uart_status,
)

MAC = "AABBCCDDEEFF"


class ProtocolBuildParseTest(unittest.TestCase):
    """Exercise packet construction, parsing, and validation."""

    def test_normalize_mac_accepts_common_formats(self) -> None:
        self.assertEqual(normalize_mac("aa:bb:cc:dd:ee:ff"), MAC)
        self.assertEqual(normalize_mac("AA-BB-CC-DD-EE-FF"), MAC)
        self.assertEqual(normalize_mac("aabb.ccdd.eeff"), MAC)

    def test_normalize_mac_rejects_invalid_values(self) -> None:
        with self.assertRaises(InvalidPacketError):
            normalize_mac("not-a-mac")
        with self.assertRaises(InvalidPacketError):
            normalize_mac("AABBCCDDEEF")

    def test_build_heartbeat_uses_outer_heartbeat_layout(self) -> None:
        request = build_heartbeat(0, "00:07:A8:B2:62:79")
        self.assertEqual(len(request), 64)
        self.assertEqual(
            request,
            b"\x00\x00\x5D\xF2"
            b"\x00\x00\x00\x00"
            b"\x00\x00\x00\x00"
            b"\x00\x00\x00\x30"
            b"\x00\x00\x00\x00"
            b"\x00\x00\x00\x00"
            b"\x00\x00\x00\x00"
            b"\x00\x00\x00\x00"
            b"\x00\x00\x00\x00"
            b"\x00\x00\x00\x00"
            b"\x00\x00\x00\x00"
            b"\x00\x00\x00\x00"
            b"\x30\x30\x30\x37\x41\x38\x42\x32\x36\x32\x37\x39"
            b"\x00\x00\x00\x00",
        )

    def test_build_heartbeat_inserts_supplied_mac(self) -> None:
        request = build_heartbeat(0, "11:22:33:44:55:66")
        self.assertEqual(request[48:60], b"112233445566")

    def test_parse_matching_heartbeat_response(self) -> None:
        response = _heartbeat_response(7, MAC)
        self.assertEqual(len(response), 68)
        parse_heartbeat_response(response, 7, MAC)

        with self.assertRaises(InvalidPacketError):
            parse_heartbeat_response(response, 8, MAC)

    def test_parse_short_outer_heartbeat_response(self) -> None:
        response = _outer_heartbeat_response(7)
        parse_heartbeat_response(response, 7, MAC)

        with self.assertRaises(InvalidPacketError):
            parse_heartbeat_response(response, 8, MAC)

    def test_parse_empty_data_response_rejected_as_heartbeat(self) -> None:
        response = _empty_data_response()

        with self.assertRaises(InvalidPacketError):
            parse_heartbeat_response(response, 7, MAC)

    def test_build_short_uart_command(self) -> None:
        frame = build_uart_short_command(Subcommand.QUERY_STATUS)
        self.assertEqual(frame[:2], b"\xFF\xFF")
        self.assertEqual(frame[2], len(frame) - 2)
        self.assertEqual(struct.unpack_from(">H", frame, 9)[0], Subcommand.QUERY_STATUS)
        self.assertEqual(frame[-1], sum(frame[2:-1]) & 0xFF)

    def test_build_set_state_and_parse_set_state_layout(self) -> None:
        frame = build_uart_set_state(
            mode=Mode.COOL,
            fan_speed=FanSpeed.HIGH,
            fan_direction=FanDirection.VERTICAL,
            power_on=True,
            target_temperature=25,
            current_temperature=27,
            current_humidity=50,
        )

        status = parse_uart_status(frame)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertTrue(status.power_on)
        self.assertEqual(status.mode, Mode.COOL)
        self.assertEqual(status.fan_speed, FanSpeed.HIGH)
        self.assertEqual(status.fan_direction, FanDirection.VERTICAL)
        self.assertEqual(status.current_temperature, 27)
        self.assertEqual(status.current_humidity, 50)
        self.assertEqual(status.target_temperature, 25)

    def test_set_state_target_temperature_is_clamped(self) -> None:
        high = parse_uart_status(
            build_uart_set_state(
                mode=Mode.HEAT,
                fan_speed=FanSpeed.AUTO,
                fan_direction=FanDirection.OFF,
                power_on=True,
                target_temperature=99,
            )
        )
        low = parse_uart_status(
            build_uart_set_state(
                mode=Mode.HEAT,
                fan_speed=FanSpeed.AUTO,
                fan_direction=FanDirection.OFF,
                power_on=True,
                target_temperature=-10,
            )
        )
        self.assertIsNotNone(high)
        self.assertIsNotNone(low)
        assert high is not None
        assert low is not None
        self.assertEqual(high.target_temperature, 30)
        self.assertEqual(low.target_temperature, 16)

    def test_command_response_parses_nested_uart_status(self) -> None:
        message_id = 42
        uart_frame = build_uart_set_state(
            mode=Mode.DRY,
            fan_speed=FanSpeed.LOW,
            fan_direction=FanDirection.BOTH,
            power_on=True,
            target_temperature=23,
        )
        response = _command_response(message_id, MAC, uart_frame)

        status = parse_command_response(response, message_id, MAC)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertTrue(status.power_on)
        self.assertEqual(status.mode, Mode.DRY)
        self.assertEqual(status.fan_speed, FanSpeed.LOW)
        self.assertEqual(status.fan_direction, FanDirection.BOTH)
        self.assertEqual(status.target_temperature, 23)

    def test_invalid_uart_checksum_is_rejected(self) -> None:
        frame = bytearray(
            build_uart_set_state(
                mode=Mode.COOL,
                fan_speed=FanSpeed.AUTO,
                fan_direction=FanDirection.OFF,
                power_on=True,
                target_temperature=24,
            )
        )
        frame[-1] ^= 0xFF

        with self.assertRaises(InvalidPacketError):
            parse_uart_status(bytes(frame))


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


def _empty_data_response() -> bytes:
    return b"".join(
        (
            b"\x00\x00",
            struct.pack(">H", DataClass.DATA_RESPONSE),
            b"\x00" * 8,
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


if __name__ == "__main__":
    unittest.main()
