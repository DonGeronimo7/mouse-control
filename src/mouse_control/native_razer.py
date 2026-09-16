"""Native read-only Razer protocol teacher.

This module deliberately does not depend on OpenRazer, its daemon, D-Bus API,
Python client, or mouse-control's optional OpenRazer runtime backend.  Public
OpenRazer/OpenMouse protocol research is used only as reference material for a
small exact-PID table and the already-documented 90-byte Razer control grammar.

The implementation exposes *read queries only*.  It has no setter API and the
packet encoder rejects command IDs whose read-direction bit is not set.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import time

from .discovery import MouseDevice
from .generic_hid import GenericHidDevice, discover_hid_devices


RAZER_VENDOR_ID = 0x1532
RAZER_REPORT_ID = 0
RAZER_PACKET_LENGTH = 90
RAZER_STATUS_BUSY = 0x01
RAZER_STATUS_OK = 0x02
RAZER_STATUS_UNSUPPORTED = 0x05

_ARGS_OFFSET = 8
_CHECKSUM_INDEX = 88
_CHECKSUM_FIRST = 2
_CHECKSUM_LAST = 88
_HID_FEATURE_BUFFER_LENGTH = RAZER_PACKET_LENGTH + 1

# Linux asm-generic/ioctl.h / hidraw.h constants.  HID feature ioctls carry
# the report number in byte zero of the userspace buffer; Razer's control
# report number is zero and the 90-byte protocol packet follows it.
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
_IOC_WRITE = 1
_IOC_READ = 2


class RazerProtocolError(RuntimeError):
    """A validated native Razer read query failed."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True)
class RazerCommand:
    """One known read-direction Razer command."""

    command_class: int
    command_id: int
    data_size: int
    args: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not (0 <= self.command_class <= 0xFF):
            raise ValueError("command_class must fit in one byte")
        if not (0 <= self.command_id <= 0xFF):
            raise ValueError("command_id must fit in one byte")
        if not (self.command_id & 0x80):
            raise ValueError("native Razer teacher forbids write-direction commands")
        if not (0 <= self.data_size <= 80):
            raise ValueError("data_size must be between 0 and 80")
        if len(self.args) > self.data_size:
            raise ValueError("command arguments exceed declared data_size")
        if any(not 0 <= value <= 0xFF for value in self.args):
            raise ValueError("command arguments must fit in one byte")


@dataclass(frozen=True)
class RazerProductSpec:
    """Exact product facts required to issue only known-safe reads."""

    model: str
    transaction_id: int
    dpi_storage: int
    high_rate_polling: bool
    has_battery: bool


@dataclass(frozen=True)
class NativeRazerState:
    """Protocol-neutral semantic state returned to the teacher boundary."""

    model: str
    firmware: str | None = None
    dpi: tuple[int, int] | None = None
    polling_rate: int | None = None
    battery: int | None = None
    charging: bool | None = None


# Exact PIDs with independent hardware evidence in the public protocol corpus.
# This is intentionally conservative.  A Razer VID by itself is never enough
# to authorize a control query because transaction IDs and polling grammars
# vary by product ID and transport.
VERIFIED_RAZER_PRODUCTS: dict[int, RazerProductSpec] = {
    0x00A5: RazerProductSpec("Viper V2 Pro (Wired)", 0x1F, 0x01, False, False),
    0x00A6: RazerProductSpec("Viper V2 Pro", 0x1F, 0x01, True, True),
    0x00B8: RazerProductSpec("Viper V3 HyperSpeed", 0x1F, 0x01, False, True),
    0x00C0: RazerProductSpec("Viper V3 Pro (Wired)", 0x1F, 0x01, False, False),
    0x00C1: RazerProductSpec("Viper V3 Pro", 0x1F, 0x01, True, True),
}

_FIRMWARE = RazerCommand(0x00, 0x81, 0x02)
_LEGACY_POLLING = RazerCommand(0x00, 0x85, 0x01)
_EXTENDED_POLLING = RazerCommand(0x00, 0xC0, 0x02, (0x00,))
_BATTERY = RazerCommand(0x07, 0x80, 0x02)
_CHARGING = RazerCommand(0x07, 0x84, 0x02)


def _dpi_command(storage: int) -> RazerCommand:
    return RazerCommand(0x04, 0x85, 0x07, (storage,))


def razer_checksum(packet: bytes | bytearray) -> int:
    """Return Razer's XOR checksum over bytes 2..87."""

    if len(packet) != RAZER_PACKET_LENGTH:
        raise ValueError(f"Razer packet must be {RAZER_PACKET_LENGTH} bytes")
    checksum = 0
    for value in packet[_CHECKSUM_FIRST:_CHECKSUM_LAST]:
        checksum ^= value
    return checksum


def encode_read_request(command: RazerCommand, transaction_id: int) -> bytes:
    """Encode one safe read request.

    ``RazerCommand`` rejects write-direction IDs at construction, giving this
    module a structural guard against accidentally becoming a setter.
    """

    if not (0 <= transaction_id <= 0xFF):
        raise ValueError("transaction_id must fit in one byte")
    packet = bytearray(RAZER_PACKET_LENGTH)
    packet[1] = transaction_id
    packet[5] = command.data_size
    packet[6] = command.command_class
    packet[7] = command.command_id
    packet[_ARGS_OFFSET:_ARGS_OFFSET + len(command.args)] = bytes(command.args)
    packet[_CHECKSUM_INDEX] = razer_checksum(packet)
    return bytes(packet)


def decode_response(packet: bytes, command: RazerCommand) -> bytes:
    """Validate a Razer response and return its argument bytes."""

    if len(packet) != RAZER_PACKET_LENGTH:
        raise RazerProtocolError(
            f"Razer response was {len(packet)} bytes, expected {RAZER_PACKET_LENGTH}"
        )
    if packet[_CHECKSUM_INDEX] != razer_checksum(packet):
        raise RazerProtocolError("Razer response checksum mismatch")

    status = packet[0]
    if status == RAZER_STATUS_BUSY:
        raise RazerProtocolError("Razer device is busy", status=status, retryable=True)
    if status == RAZER_STATUS_UNSUPPORTED:
        raise RazerProtocolError("Razer command is unsupported", status=status)
    if status != RAZER_STATUS_OK:
        raise RazerProtocolError(
            f"Razer command failed with status 0x{status:02x}", status=status
        )
    if packet[6] != command.command_class or packet[7] != command.command_id:
        raise RazerProtocolError("Razer response belongs to a different command", retryable=True)

    length = min(packet[5], 80)
    return packet[_ARGS_OFFSET:_ARGS_OFFSET + length]


def _ioc(direction: int, type_: int, nr: int, size: int) -> int:
    return (
        (direction << _IOC_DIRSHIFT)
        | (type_ << _IOC_TYPESHIFT)
        | (nr << _IOC_NRSHIFT)
        | (size << _IOC_SIZESHIFT)
    )


def _hid_feature_ioctl(number: int, length: int) -> int:
    return _ioc(_IOC_READ | _IOC_WRITE, ord("H"), number, length)


class HidrawRazerSession:
    """Minimal Linux hidraw Feature-report transport for native Razer reads."""

    def __init__(
        self,
        path: str | Path,
        *,
        response_delay: float = 0.1,
        response_attempts: int = 6,
        sleep: Callable[[float], None] = time.sleep,
        ioctl: Callable[..., object] = fcntl.ioctl,
    ) -> None:
        if response_delay < 0:
            raise ValueError("response_delay cannot be negative")
        if response_attempts < 1:
            raise ValueError("response_attempts must be at least one")
        self.path = Path(path)
        self.response_delay = response_delay
        self.response_attempts = response_attempts
        self._sleep = sleep
        self._ioctl = ioctl
        self._fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
        self.closed = False

    def _set_feature(self, packet: bytes) -> None:
        if len(packet) != RAZER_PACKET_LENGTH:
            raise ValueError("invalid Razer packet length")
        buffer = bytearray(_HID_FEATURE_BUFFER_LENGTH)
        buffer[0] = RAZER_REPORT_ID
        buffer[1:] = packet
        self._ioctl(
            self._fd,
            _hid_feature_ioctl(0x06, len(buffer)),
            buffer,
            True,
        )

    def _get_feature(self) -> bytes:
        buffer = bytearray(_HID_FEATURE_BUFFER_LENGTH)
        buffer[0] = RAZER_REPORT_ID
        self._ioctl(
            self._fd,
            _hid_feature_ioctl(0x07, len(buffer)),
            buffer,
            True,
        )
        return bytes(buffer[1:])

    def query(self, command: RazerCommand, transaction_id: int) -> bytes:
        """Execute one proven getter and return validated argument bytes.

        The request Feature SET is part of Razer's read transaction.  The
        request is sent once; only Feature GET response reads are retried.
        """

        request = encode_read_request(command, transaction_id)
        self._set_feature(request)

        last_error: RazerProtocolError | None = None
        for _attempt in range(self.response_attempts):
            self._sleep(self.response_delay)
            try:
                return decode_response(self._get_feature(), command)
            except RazerProtocolError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
        raise last_error or RazerProtocolError("Razer device produced no usable response")

    def close(self) -> None:
        if not self.closed:
            os.close(self._fd)
            self.closed = True


def _decode_firmware(args: bytes) -> str | None:
    if len(args) < 2:
        return None
    return f"{args[0]}.{args[1]}"


def _decode_dpi(args: bytes) -> tuple[int, int] | None:
    if len(args) < 5:
        return None
    x = (args[1] << 8) | args[2]
    y = (args[3] << 8) | args[4]
    if x <= 0 or y <= 0:
        return None
    return x, y


def _decode_polling(args: bytes, *, extended: bool) -> int | None:
    index = 1 if extended else 0
    ceiling = 8000 if extended else 1000
    if len(args) <= index or args[index] == 0:
        return None
    divisor = args[index]
    if ceiling % divisor:
        return None
    rate = ceiling // divisor
    return rate if rate > 0 else None


def _decode_battery(args: bytes) -> int | None:
    if len(args) < 2:
        return None
    return max(0, min(100, round(args[1] * 100 / 255)))


def _decode_charging(args: bytes) -> bool | None:
    if len(args) < 2:
        return None
    if args[1] not in (0, 1):
        return None
    return args[1] == 1


def _safe_query(
    session: HidrawRazerSession,
    command: RazerCommand,
    transaction_id: int,
) -> bytes | None:
    try:
        return session.query(command, transaction_id)
    except (OSError, RazerProtocolError):
        return None


def read_native_razer_state(
    device: MouseDevice,
    *,
    discovery: Callable[[MouseDevice], list[GenericHidDevice]] = discover_hid_devices,
    session_factory: Callable[[str | Path], HidrawRazerSession] = HidrawRazerSession,
) -> NativeRazerState | None:
    """Read semantic state through mouse-control's own native Razer protocol.

    Applicability is exact VID:PID.  Every matching hidraw interface may be
    tested with the harmless firmware getter, but exactly one validated
    responder must bind.  Multiple responders are treated as ambiguous.
    """

    if device.vendor != RAZER_VENDOR_ID or device.product is None:
        return None
    spec = VERIFIED_RAZER_PRODUCTS.get(device.product)
    if spec is None:
        return None

    responders: list[tuple[HidrawRazerSession, bytes]] = []
    opened: list[HidrawRazerSession] = []
    try:
        for hid_device in discovery(device):
            try:
                session = session_factory(hid_device.path)
            except OSError:
                continue
            opened.append(session)
            try:
                firmware = session.query(_FIRMWARE, spec.transaction_id)
            except (OSError, RazerProtocolError):
                continue
            responders.append((session, firmware))

        if len(responders) > 1:
            raise RazerProtocolError(
                "multiple hidraw interfaces answered the native Razer protocol; "
                "refusing ambiguous teacher evidence"
            )
        if not responders:
            return None

        session, firmware_args = responders[0]
        dpi_args = _safe_query(session, _dpi_command(spec.dpi_storage), spec.transaction_id)
        polling_command = _EXTENDED_POLLING if spec.high_rate_polling else _LEGACY_POLLING
        polling_args = _safe_query(session, polling_command, spec.transaction_id)

        battery = None
        charging = None
        if spec.has_battery:
            battery_args = _safe_query(session, _BATTERY, spec.transaction_id)
            charging_args = _safe_query(session, _CHARGING, spec.transaction_id)
            if battery_args is not None:
                battery = _decode_battery(battery_args)
            if charging_args is not None:
                charging = _decode_charging(charging_args)

        return NativeRazerState(
            model=spec.model,
            firmware=_decode_firmware(firmware_args),
            dpi=_decode_dpi(dpi_args) if dpi_args is not None else None,
            polling_rate=(
                _decode_polling(polling_args, extended=spec.high_rate_polling)
                if polling_args is not None
                else None
            ),
            battery=battery,
            charging=charging,
        )
    finally:
        for session in opened:
            session.close()
