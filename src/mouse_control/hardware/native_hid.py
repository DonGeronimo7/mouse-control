"""Protocol-neutral native HID backend."""

from __future__ import annotations

from collections.abc import Callable
import logging

from ..discovery import MouseDevice
from ..generic_hid import discover_hid_devices
from ..hid_session import HidSession
from ..hidpp import HidppError, LOGITECH_VENDOR_ID
from ..hidpp_driver import (HOST_MODE, ONBOARD_MODE, Hidpp20Driver,
                            connect_hidpp20)
from .base import HardwareBackend, HardwareError
from .capabilities import BatteryState, DpiState, HardwareCapabilities

LOG = logging.getLogger(__name__)

# Host-mode takeover changes who owns hardware behavior (notably DPI cycling).
# Keep knowledge of validated identities separate from ordinary capability
# reporting so discovery/reconnect paths can preserve native behavior.
HOST_MODE_TRANSITION_ALLOWLIST = frozenset({(3, 0x046D, 0x4074)})


class NativeHidBackend(HardwareBackend):
    name = "Native HID"

    def __init__(self, *, discovery=discover_hid_devices,
                 session_factory=HidSession,
                 connectors: tuple[Callable[[HidSession], Hidpp20Driver], ...] =
                 (connect_hidpp20,)) -> None:
        self._discovery = discovery
        self._session_factory = session_factory
        self._connectors = connectors
        self._bound: dict[MouseDevice, tuple[HidSession, Hidpp20Driver]] = {}

    def supports_device(self, device: MouseDevice) -> bool:
        if device in self._bound:
            return True
        if device.vendor != LOGITECH_VENDOR_ID or device.product is None:
            return False
        candidates: list[tuple[HidSession, Hidpp20Driver]] = []
        for interface in self._discovery(device):
            try:
                session = self._session_factory(interface.path)
            except OSError:
                continue
            try:
                for connector in self._connectors:
                    try:
                        candidates.append((session, connector(session)))
                        break
                    except HidppError:
                        continue
                else:
                    session.close()
            except Exception:
                session.close()
                raise
        if len(candidates) > 1:
            for session, _driver in candidates:
                session.close()
            raise HardwareError("Native HID: multiple interfaces claimed the protocol; refusing ambiguous writes")
        if not candidates:
            return False
        self._bound[device] = candidates[0]
        return True

    def _driver(self, device: MouseDevice) -> Hidpp20Driver:
        bound = self._bound.get(device)
        if bound is not None and getattr(bound[0], "closed", False):
            bound[0].close()
            del self._bound[device]
        if not self.supports_device(device):
            raise HardwareError("Native HID: no validated protocol driver")
        return self._bound[device][1]

    def get_capabilities(self, device: MouseDevice) -> HardwareCapabilities:
        return self._driver(device).capabilities

    def get_device_name(self, device: MouseDevice) -> str | None:
        return self._driver(device).name or device.name

    def supports_dpi(self, device: MouseDevice) -> bool:
        caps = self.get_capabilities(device).dpi
        return caps.readable and caps.writable

    def supports_battery(self, device: MouseDevice) -> bool:
        return self.get_capabilities(device).battery.readable

    def get_battery_state(self, device: MouseDevice) -> BatteryState | None:
        try:
            return self._driver(device).get_battery_state()
        except HidppError as exc:
            raise HardwareError(f"Native HID: {exc}") from exc

    def supports_dpi_monitoring(self, device: MouseDevice) -> bool:
        return self.get_capabilities(device).dpi.readable

    def supports_dpi_events(self, device: MouseDevice) -> bool:
        return self.get_capabilities(device).dpi.events

    def get_dpi_state(self, device: MouseDevice) -> DpiState | None:
        try:
            return self._driver(device).get_dpi_state()
        except HidppError as exc:
            raise HardwareError(f"Native HID: {exc}") from exc

    def get_dpi(self, device: MouseDevice) -> int | tuple[int, int] | None:
        state = self.get_dpi_state(device)
        return state.display_value if state else None

    def get_dpi_values(self, device: MouseDevice) -> list[int]:
        caps = self.get_capabilities(device).dpi
        values = list(caps.values or ())
        for item in caps.ranges or ():
            values.extend(range(item.minimum, item.maximum + 1, item.step))
        return sorted(set(values))

    def set_dpi(self, device: MouseDevice, dpi: int) -> DpiState:
        try:
            return self._driver(device).set_dpi(dpi)
        except HidppError as exc:
            raise HardwareError(f"Native HID: {exc}") from exc

    def supports_polling_rate(self, device: MouseDevice) -> bool:
        return self.get_capabilities(device).report_rate.readable

    def supports_polling_rate_writes(self, device: MouseDevice) -> bool:
        """Return whether a proven post-discovery path can write report rate.

        This includes a hardware-validated Onboard -> Host transition. Automatic
        discovery/reconnect code must instead call
        ``supports_polling_rate_writes_without_takeover``.
        """

        driver = self._driver(device)
        if not driver.capabilities.report_rate.writable:
            return False
        try:
            mode = driver.get_control_mode()
        except HidppError:
            return False
        return mode == HOST_MODE or self._may_enter_host_mode(device)

    def supports_polling_rate_writes_without_takeover(self, device: MouseDevice) -> bool:
        """Return whether report rate can change without changing control ownership."""

        driver = self._driver(device)
        if not driver.capabilities.report_rate.writable:
            return False
        try:
            return driver.get_control_mode() == HOST_MODE
        except HidppError:
            return False

    @staticmethod
    def _may_enter_host_mode(device: MouseDevice) -> bool:
        """Whether an explicit post-discovery Host takeover is hardware-validated."""
        return (device.bustype, device.vendor, device.product) in HOST_MODE_TRANSITION_ALLOWLIST

    def get_polling_rates(self, device: MouseDevice) -> list[int]:
        return list(self.get_capabilities(device).report_rate.values or ())

    def get_polling_rate(self, device: MouseDevice) -> int | None:
        try:
            return self._driver(device).get_report_rate()
        except HidppError as exc:
            raise HardwareError(f"Native HID: {exc}") from exc

    def set_polling_rate(self, device: MouseDevice, hz: int) -> None:
        """Execute a proven polling write, taking Host mode only when authorized.

        Ordinary discovery and automatic reconciliation never call this while
        takeover would be required. The transition remains available as a
        post-discovery execution primitive for an explicit user configuration.
        """

        driver = self._driver(device)
        caps = driver.capabilities.report_rate
        if not caps.readable or not caps.writable or not caps.values or hz not in caps.values:
            raise HardwareError(f"Native HID: unsupported polling rate {hz} Hz")
        original_mode: int | None = None
        transition_attempted = False
        try:
            original_mode = driver.get_control_mode()
            if original_mode == ONBOARD_MODE:
                if not self._may_enter_host_mode(device):
                    raise HardwareError(
                        "Native HID: automatic Onboard -> Host transition is not "
                        "validated for this transport and VID:PID")
                transition_attempted = True
                driver.set_control_mode(HOST_MODE)
                actual_mode = driver.get_control_mode()
                if actual_mode != HOST_MODE:
                    raise HardwareError(
                        f"Native HID: Host mode verification failed; read 0x{actual_mode:02x}")
            elif original_mode != HOST_MODE:
                raise HardwareError(
                    f"Native HID: unsupported control mode 0x{original_mode:02x}")
            driver.set_report_rate(hz)
        except HidppError as exc:
            failure: Exception = HardwareError(f"Native HID: {exc}")
        except HardwareError as exc:
            failure = exc
        except Exception as exc:
            failure = HardwareError(f"Native HID: polling-rate transaction failed: {exc}")
        else:
            # Explicit post-discovery takeover intentionally remains in Host mode.
            return

        if transition_attempted and original_mode is not None:
            try:
                driver.set_control_mode(original_mode)
                restored = driver.get_control_mode()
                if restored != original_mode:
                    raise HidppError(
                        f"mode rollback verification read 0x{restored:02x}, "
                        f"expected 0x{original_mode:02x}")
            except Exception as rollback_exc:
                raise HardwareError(
                    f"{failure}; additionally failed to restore original control mode: "
                    f"{rollback_exc}") from failure
        raise failure

    def watch_dpi_events(self, device: MouseDevice, callback,
                         shutdown_event, ready_callback=None) -> None:
        # The supervisor owns retries. A failed subscription must discard this
        # session and trigger fresh backend discovery instead of becoming sticky.
        self._driver(device).watch_dpi(callback, shutdown_event, ready_callback)

    def close(self) -> None:
        for session, _driver in self._bound.values():
            session.close()
        self._bound.clear()