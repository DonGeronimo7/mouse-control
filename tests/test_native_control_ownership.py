"""Control-ownership regressions for discovery-first hardware handling."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from mouse_control.discovery import MouseDevice
from mouse_control.hardware import DesiredHardwareState, HardwareSupervisor
from mouse_control.hardware.native_hid import NativeHidBackend
from mouse_control.hidpp_driver import Hidpp20Driver, HOST_MODE, ONBOARD_MODE

from test_native_hid import FakeSession


G305 = MouseDevice(
    "G305", "/dev/fake", vendor=0x046D, product=0x4074, bustype=3
)


class ClosableFakeSession(FakeSession):
    def close(self):
        pass


def native_backend(session: ClosableFakeSession) -> NativeHidBackend:
    return NativeHidBackend(
        discovery=lambda _device: [SimpleNamespace(path="/dev/fake")],
        session_factory=lambda _path: session,
        connectors=(lambda selected: Hidpp20Driver(selected, 4),),
    )


def test_onboard_polling_is_not_safe_for_automatic_reconciliation():
    session = ClosableFakeSession(profile_mode=ONBOARD_MODE)
    backend = native_backend(session)
    try:
        assert backend.supports_polling_rate_writes(G305)
        assert not backend.supports_polling_rate_writes_without_takeover(G305)
        assert session.profile_mode == ONBOARD_MODE
    finally:
        backend.close()


def test_existing_host_mode_is_safe_without_an_ownership_transition():
    session = ClosableFakeSession(profile_mode=HOST_MODE)
    backend = native_backend(session)
    try:
        assert backend.supports_polling_rate_writes_without_takeover(G305)
    finally:
        backend.close()


def test_supervisor_preserves_onboard_mode_instead_of_replaying_polling_write():
    session = ClosableFakeSession(profile_mode=ONBOARD_MODE)
    session.rate_ms = 1
    backend = native_backend(session)
    supervisor = HardwareSupervisor(
        backend,
        G305,
        lambda _device: MagicMock(),
        DesiredHardwareState(polling_rate_hz=500),
    )
    try:
        session.calls.clear()
        supervisor.reconcile()
        assert session.profile_mode == ONBOARD_MODE
        assert session.rate_ms == 1
        assert not any(call[1:3] == (0x12, 1) for call in session.calls)
        assert not any(call[1:3] == (0x17, 2) for call in session.calls)
    finally:
        supervisor.close()