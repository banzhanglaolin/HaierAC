"""Binary protocol helpers for Haier AC local TCP payloads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import re
import struct
from typing import TypeVar

MIN_TEMP = 16
MAX_TEMP = 30
_IntEnumT = TypeVar("_IntEnumT", bound=IntEnum)


class HaierProtocolError(Exception):
    """Base error for Haier protocol failures."""


class InvalidPacketError(HaierProtocolError):
    """Raised when a packet is malformed or unexpected."""


class DataClass(IntEnum):
    """Outer TCP payload types."""

    DATA_REQUEST = 0x2714
    DATA_RESPONSE = 0x2715
    HEARTBEAT_REQUEST = 0x5DF2
    HEARTBEAT_RESPONSE = 0x5DF3
    DISCONNECT_REQUEST = 0x65F6
    DISCONNECT_RESPONSE = 0x65F7


class UartFrameType(IntEnum):
    """Inner UART control byte types."""

    QUERY_OR_SET = 0x01
    RETURN_DATA = 0x02
    INVALID_COMMAND = 0x03
    ALARM_REPORT = 0x04
    ACK = 0x05
    ACTIVE_REPORT = 0x06
    STOP_FAULT_ALARM = 0x09
    ALARM = 0x73
    ALARM_RESPONSE = 0x74
    GET_NETWORK_STATUS = 0xF0
    GET_NETWORK_STATUS_RESPONSE = 0xF1

    CONTROL = 0x01
    STATUS = 0x02
    INVALID = 0x03
    ALARM_STATUS = 0x04
    CONFIRM = 0x05
    REPORT = 0x06
    GET_ALARM_STATUS = 0x73
    GET_ALARM_STATUS_RESPONSE = 0x74


class UartDirection(IntEnum):
    """Inner UART frame directions."""

    MODULE_TO_BOARD = 0x0000
    BOARD_TO_MODULE = 0x0001


class Subcommand(IntEnum):
    """UART control subcommands."""

    QUERY_STATUS = 0x4D01
    TURN_ON = 0x4D02
    TURN_OFF = 0x4D03
    SET_STATE = 0x4D5F


class Mode(IntEnum):
    """Haier AC operating modes."""

    PMV = 0
    COOL = 1
    HEAT = 2
    FAN = 3
    DRY = 4


class FanSpeed(IntEnum):
    """Haier AC fan speeds."""

    HIGH = 0
    MEDIUM = 1
    LOW = 2
    AUTO = 3


class FanDirection(IntEnum):
    """Haier AC swing modes."""

    OFF = 0
    VERTICAL = 1
    HORIZONTAL = 2
    BOTH = 3


AC_STATE_ON = 0x01
AC_AUX_HEAT_ON = 0x02
AC_HEALTH_ON = 0x08


@dataclass(slots=True)
class ACStatus:
    """Best-effort decoded state from a UART status frame."""

    power_on: bool = False
    mode: Mode = Mode.PMV
    fan_speed: FanSpeed = FanSpeed.AUTO
    fan_direction: FanDirection = FanDirection.OFF
    current_temperature: float | None = None
    current_humidity: float | None = None
    target_temperature: float | None = 24
    aux_heat_on: bool = False
    health_on: bool = False


def normalize_mac(mac: str) -> str:
    """Normalize a MAC address to the 12-byte ASCII form used by the protocol."""
    normalized = re.sub(r"[^0-9A-Fa-f]", "", mac).upper()
    if len(normalized) != 12 or not re.fullmatch(r"[0-9A-F]{12}", normalized):
        raise InvalidPacketError("MAC address must contain 12 hexadecimal characters")
    return normalized


def build_heartbeat(message_id: int, mac: str) -> bytes:
    """Build an outer heartbeat request."""
    return b"".join(
        (
            b"\x00\x00",
            struct.pack(">H", DataClass.HEARTBEAT_REQUEST),
            b"\x00" * 4,
            struct.pack(">I", message_id),
            struct.pack(">I", 48),
            b"\x00" * 32,
            normalize_mac(mac).encode("ascii"),
            b"\x00" * 4,
        )
    )


def build_disconnect(message_id: int) -> bytes:
    """Build an outer disconnect request."""
    return b"".join(
        (
            b"\x00\x00",
            struct.pack(">H", DataClass.DISCONNECT_REQUEST),
            b"\x00" * 4,
            struct.pack(">I", message_id),
            b"\x00" * 4,
        )
    )


def build_command(message_id: int, mac: str, uart_frame: bytes) -> bytes:
    """Build an outer data request containing an inner UART frame."""
    return b"".join(
        (
            b"\x00\x00",
            struct.pack(">H", DataClass.DATA_REQUEST),
            b"\x00" * 4,
            b"\x00" * 32,
            normalize_mac(mac).encode("ascii"),
            b"\x00" * 20,
            struct.pack(">I", message_id),
            struct.pack(">I", len(uart_frame)),
            uart_frame,
        )
    )


def build_uart_short_command(subcommand: Subcommand) -> bytes:
    """Build a short UART control command."""
    frame = bytearray()
    frame.extend(b"\xFF\xFF")
    frame.append(0)
    frame.extend(b"\x00" * 4)
    frame.extend(struct.pack(">H", UartDirection.MODULE_TO_BOARD))
    frame.append(UartFrameType.QUERY_OR_SET)
    frame.extend(struct.pack(">H", subcommand))
    frame.append(0)
    frame[2] = len(frame) - 3
    frame[-1] = _checksum(frame)
    return bytes(frame)


def build_uart_set_state(
    *,
    mode: Mode,
    fan_speed: FanSpeed,
    fan_direction: FanDirection,
    power_on: bool,
    target_temperature: float | None,
    current_temperature: float | None = None,
    current_humidity: float | None = None,
    aux_heat_on: bool = False,
    health_on: bool = False,
) -> bytes:
    """Build the longer UART command that carries full AC state."""
    fan_speed = _fan_speed_for_mode(mode, fan_speed)
    target_raw = _encode_target_temperature(target_temperature)
    frame = bytearray()
    frame.extend(b"\xFF\xFF")
    frame.append(0)
    frame.extend(b"\x00" * 4)
    frame.extend(struct.pack(">H", UartDirection.MODULE_TO_BOARD))
    frame.append(UartFrameType.QUERY_OR_SET)
    frame.extend(struct.pack(">H", Subcommand.SET_STATE))
    frame.extend(b"\x00" * 4)
    frame.extend(b"\x00" * 6)
    frame.extend(struct.pack(">H", mode))
    frame.extend(struct.pack(">H", fan_speed))
    frame.extend(struct.pack(">H", fan_direction))
    frame.extend(
        struct.pack(
            ">H", _encode_power_options(power_on, mode, aux_heat_on, health_on)
        )
    )
    frame.extend(b"\x00" * 4)
    frame.extend(struct.pack(">H", target_raw))
    frame.append(0)
    frame[2] = len(frame) - 3
    frame[-1] = _checksum(frame)
    return bytes(frame)


def parse_heartbeat_response(data: bytes, message_id: int, mac: str) -> None:
    """Validate a heartbeat response."""
    if len(data) < 4:
        raise InvalidPacketError("heartbeat response too short")
    if _u16(data, 2) != DataClass.HEARTBEAT_RESPONSE:
        raise InvalidPacketError("unexpected heartbeat response type")

    if len(data) == 16:
        if _u32(data, 8) != message_id:
            raise InvalidPacketError("unexpected heartbeat message id")
        if _u32(data, 12) != 0:
            raise InvalidPacketError("invalid heartbeat payload length")
        return

    if len(data) < 64:
        raise InvalidPacketError("heartbeat response too short")
    if _u32(data, 8) != message_id:
        raise InvalidPacketError("unexpected heartbeat message id")
    payload_len = _u32(data, 12)
    if payload_len < 48:
        raise InvalidPacketError("invalid heartbeat payload length")
    if payload_len + 16 != len(data):
        raise InvalidPacketError("invalid heartbeat payload length")
    if data[48:60].decode("ascii", errors="ignore") != normalize_mac(mac):
        raise InvalidPacketError("unexpected heartbeat MAC address")


def parse_disconnect_response(data: bytes, message_id: int) -> None:
    """Validate an outer disconnect response."""
    if len(data) != 16:
        raise InvalidPacketError("disconnect response has invalid length")
    if _u16(data, 2) != DataClass.DISCONNECT_RESPONSE:
        raise InvalidPacketError("unexpected disconnect response type")
    if _u32(data, 8) != message_id:
        raise InvalidPacketError("unexpected disconnect message id")


def parse_command_response(data: bytes, message_id: int, mac: str) -> ACStatus | None:
    """Validate an outer data response and decode the nested UART status if present."""
    if len(data) < 80:
        raise InvalidPacketError("command response too short")
    if _u16(data, 2) != DataClass.DATA_RESPONSE:
        raise InvalidPacketError("unexpected command response type")
    if _u32(data, 72) != message_id:
        raise InvalidPacketError("unexpected command message id")
    if data[40:52].decode("ascii", errors="ignore") != normalize_mac(mac):
        raise InvalidPacketError("unexpected command MAC address")

    uart_len = _u32(data, 76)
    if len(data) != 80 + uart_len:
        raise InvalidPacketError("invalid UART payload length")
    return parse_uart_status(data[80:])


def parse_uart_status(frame: bytes) -> ACStatus | None:
    """Decode a UART status frame.

    The repository's current samples show a REPORT layout that is a few bytes
    longer than the SET_STATE command layout. This parser accepts both forms and
    only returns fields that can be decoded safely.
    """
    if len(frame) < 12:
        return None
    if frame[:2] != b"\xFF\xFF":
        raise InvalidPacketError("invalid UART header")
    frame = _strip_trailing_uart_delimiter(frame)
    if frame[2] not in {len(frame) - 2, len(frame) - 3}:
        raise InvalidPacketError("invalid UART data length")
    if not _valid_uart_checksum(frame):
        raise InvalidPacketError("invalid UART checksum")

    frame_type = _safe_int_enum(UartFrameType, frame[9])
    if len(frame) >= 37 and frame_type in {
        UartFrameType.RETURN_DATA,
        UartFrameType.ACTIVE_REPORT,
    }:
        return _parse_report_layout(frame)
    if len(frame) >= 32:
        return _parse_set_state_layout(frame)
    return None


def _parse_report_layout(frame: bytes) -> ACStatus:
    power_options = _u16(frame, 28)
    mode = _safe_int_enum(Mode, _u16(frame, 22), Mode.PMV)
    fan_speed = _fan_speed_for_mode(
        mode, _safe_int_enum(FanSpeed, _u16(frame, 24), FanSpeed.AUTO)
    )
    return ACStatus(
        power_on=bool(power_options & AC_STATE_ON),
        mode=mode,
        fan_speed=fan_speed,
        fan_direction=_safe_int_enum(FanDirection, _u16(frame, 26), FanDirection.OFF),
        current_temperature=_plausible_temperature(_u16(frame, 12)),
        current_humidity=_plausible_humidity(_u16(frame, 14)),
        target_temperature=_decode_target_temperature(_u16(frame, 34)),
        aux_heat_on=bool(power_options & AC_AUX_HEAT_ON),
        health_on=bool(power_options & AC_HEALTH_ON),
    )


def _parse_set_state_layout(frame: bytes) -> ACStatus:
    power_options = _u16(frame, 28)
    mode = _safe_int_enum(Mode, _u16(frame, 22), Mode.PMV)
    fan_speed = _fan_speed_for_mode(
        mode, _safe_int_enum(FanSpeed, _u16(frame, 24), FanSpeed.AUTO)
    )
    return ACStatus(
        power_on=bool(power_options & AC_STATE_ON),
        mode=mode,
        fan_speed=fan_speed,
        fan_direction=_safe_int_enum(FanDirection, _u16(frame, 26), FanDirection.OFF),
        current_temperature=_plausible_temperature(_u16(frame, 12)),
        current_humidity=_plausible_humidity(_u16(frame, 14)),
        target_temperature=_decode_target_temperature(_u16(frame, 34)),
        aux_heat_on=bool(power_options & AC_AUX_HEAT_ON),
        health_on=bool(power_options & AC_HEALTH_ON),
    )


def _checksum(frame: bytearray) -> int:
    return sum(frame[2:-1]) & 0xFF


def _fan_speed_for_mode(mode: Mode, fan_speed: FanSpeed) -> FanSpeed:
    if mode == Mode.FAN and fan_speed == FanSpeed.AUTO:
        return FanSpeed.HIGH
    return fan_speed


def _encode_power_options(
    power_on: bool,
    mode: Mode,
    aux_heat_on: bool = False,
    health_on: bool = False,
) -> int:
    if not power_on:
        return 0
    value = 0
    value |= AC_STATE_ON
    if aux_heat_on and mode == Mode.HEAT:
        value |= AC_AUX_HEAT_ON
    if health_on:
        value |= AC_HEALTH_ON
    return value


def _valid_uart_checksum(frame: bytes) -> bool:
    expected = sum(frame[2:-1]) & 0xFF
    return frame[-1] == expected or frame[-1] == (sum(frame[2:]) & 0xFF) or (sum(frame[2:]) & 0xFF) == 0


def _strip_trailing_uart_delimiter(frame: bytes) -> bytes:
    if frame[-1] != 0x55:
        return frame

    stripped = frame[:-1]
    if (
        len(stripped) >= 12
        and stripped[2] in {len(stripped) - 2, len(stripped) - 3}
        and _valid_uart_checksum(stripped)
    ):
        return stripped
    return frame


def _encode_target_temperature(value: float | None) -> int:
    if value is None:
        return 24 - MIN_TEMP
    bounded = min(MAX_TEMP, max(MIN_TEMP, round(value)))
    return bounded - MIN_TEMP


def _decode_target_temperature(raw: int) -> float | None:
    if 0 <= raw <= MAX_TEMP - MIN_TEMP:
        return float(MIN_TEMP + raw)
    if MIN_TEMP <= raw <= MAX_TEMP:
        return float(raw)
    return None


def _plausible_temperature(value: int) -> float | None:
    if 0 <= value <= 55:
        return float(value)
    return None


def _plausible_humidity(value: int) -> float | None:
    if 0 <= value <= 100:
        return float(value)
    return None


def _safe_int_enum(
    enum_type: type[_IntEnumT],
    value: int,
    default: _IntEnumT | None = None,
) -> _IntEnumT | None:
    try:
        return enum_type(value)
    except ValueError:
        return default


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]
