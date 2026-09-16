"""Native Razer teacher protocol regressions."""

from pathlib import Path

import pytest

from mouse_control.discovery import MouseDevice
from mouse_control.generic_hid import GenericHidDevice
from mouse_control.native_razer import (
    HidrawRazerSession,
    RAZER_PACKET_LENGTH,
    NativeRazerState,
    RazerCommand,
    RazerProtocolError,
    _FIRMWARE,
    _dpi_command,
    decode_response,
    encode_read_request,
    razer_checksum,
    read_native_razer_state,
)


def viper_v3_pro_wireless(product=0x00C1):
    return MouseDevice(
        "Razer Viper V3 Pro",
        "/dev/input/event20",
        vendor=0x1532,
        product=product,
        bustype=3,
    )


def hid(path: str):
    return GenericHidDevice(
        path=Path(path),
        vendor_id=0x1532,
        product_id=0x00C1,
        name="Razer Viper V3 Pro",
        phys="usb-test",
        report_descriptor=b"\x05\x01",
    )


def reply(command: RazerCommand, args: bytes, status: int = 0x02) -> bytes:
    packet = bytearray(RAZER_PACKET_LENGTH)
    packet[0] = status
    packet[5] = len(args)
    packet[6] = command.command_class
    packet[7] = command.command_id
    packet[8:8 + len(args)] = args
    packet[88] = razer_checksum(packet)
    return bytes(packet)


def test_read_command_rejects_write_direction_ids():
    with pytest.raises(ValueError, match="write-direction"):
        RazerCommand(0x04, 0x05, 0x07)


def test_encoded_request_is_90_bytes_and_checksum_valid():
    command = _dpi_command(0x01)
    packet = encode_read_request(command, 0x1F)

    assert len(packet) == 90
    assert packet[1] == 0x1F
    assert packet[5:9] == bytes((0x07, 0x04, 0x85, 0x01))
    assert packet[88] == razer_checksum(packet)


def test_response_rejects_checksum_mismatch():
    packet = bytearray(reply(_FIRMWARE, b"\x01\x0c"))
    packet[20] ^= 0x01

    with pytest.raises(RazerProtocolError, match="checksum"):
        decode_response(bytes(packet), _FIRMWARE)


def test_response_rejects_different_command_as_retryable():
    packet = bytearray(reply(_FIRMWARE, b"\x01\x0c"))
    packet[7] = 0x82
    packet[88] = razer_checksum(packet)

    with pytest.raises(RazerProtocolError) as error:
        decode_response(bytes(packet), _FIRMWARE)
    assert error.value.retryable is True


def test_busy_reply_is_retryable():
    packet = reply(_FIRMWARE, b"", status=0x01)
    with pytest.raises(RazerProtocolError) as error:
        decode_response(packet, _FIRMWARE)
    assert error.value.retryable is True
    assert error.value.status == 0x01


class FakeSession:
    def __init__(self, path, *, answers=None):
        self.path = Path(path)
        self.closed = False
        self.commands = []
        self.answers = answers or {}

    def query(self, command, transaction_id):
        self.commands.append((command, transaction_id))
        key = (command.command_class, command.command_id)
        value = self.answers.get(key)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise RazerProtocolError("unsupported")
        return value

    def close(self):
        self.closed = True


def verified_answers():
    return {
        (0x00, 0x81): b"\x01\x0c",  # firmware 1.12
        (0x04, 0x85): b"\x01\x06\x40\x06\x40\x00\x00",  # 1600/1600
        (0x00, 0xC0): b"\x00\x08",  # 8000 / 8 = 1000 Hz
        (0x07, 0x80): b"\x00\xe6",  # about 90%
        (0x07, 0x84): b"\x00\x01",  # charging
    }


def test_exact_verified_pid_reads_semantics_without_runtime_backend():
    session = FakeSession("/dev/hidraw9", answers=verified_answers())
    state = read_native_razer_state(
        viper_v3_pro_wireless(),
        discovery=lambda _device: [hid("/dev/hidraw9")],
        session_factory=lambda _path: session,
    )

    assert state == NativeRazerState(
        model="Viper V3 Pro",
        firmware="1.12",
        dpi=(1600, 1600),
        polling_rate=1000,
        battery=90,
        charging=True,
    )
    assert session.closed is True
    assert all(command.command_id & 0x80 for command, _transaction in session.commands)
    assert all(transaction == 0x1F for _command, transaction in session.commands)


def test_unknown_razer_pid_is_refused_before_hid_discovery():
    called = []
    state = read_native_razer_state(
        viper_v3_pro_wireless(product=0xFFFF),
        discovery=lambda _device: called.append(True),
    )
    assert state is None
    assert called == []


def test_non_razer_device_is_refused_before_hid_discovery():
    device = MouseDevice(
        "Not Razer",
        "/dev/input/event3",
        vendor=0x1234,
        product=0x00C1,
        bustype=3,
    )
    called = []
    assert read_native_razer_state(device, discovery=lambda _device: called.append(True)) is None
    assert called == []


def test_multiple_native_razer_responders_are_rejected_as_ambiguous():
    sessions = {
        Path("/dev/hidraw1"): FakeSession("/dev/hidraw1", answers=verified_answers()),
        Path("/dev/hidraw2"): FakeSession("/dev/hidraw2", answers=verified_answers()),
    }

    with pytest.raises(RazerProtocolError, match="multiple hidraw"):
        read_native_razer_state(
            viper_v3_pro_wireless(),
            discovery=lambda _device: [hid("/dev/hidraw1"), hid("/dev/hidraw2")],
            session_factory=lambda path: sessions[Path(path)],
        )
    assert all(session.closed for session in sessions.values())


def test_optional_semantic_read_failure_does_not_erase_other_labels():
    answers = verified_answers()
    answers[(0x07, 0x80)] = RazerProtocolError("battery sleeping")
    session = FakeSession("/dev/hidraw9", answers=answers)

    state = read_native_razer_state(
        viper_v3_pro_wireless(),
        discovery=lambda _device: [hid("/dev/hidraw9")],
        session_factory=lambda _path: session,
    )
    assert state is not None
    assert state.dpi == (1600, 1600)
    assert state.polling_rate == 1000
    assert state.battery is None
    assert state.charging is True


def test_native_session_exposes_no_public_setter_api():
    public = {name for name in dir(HidrawRazerSession) if not name.startswith("_")}
    assert "query" in public
    assert not any(name.startswith("set") or name.startswith("write") for name in public)
