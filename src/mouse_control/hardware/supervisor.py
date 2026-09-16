"""Single owner for a device's optional hardware-control lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
import threading
from collections.abc import Callable

from ..discovery import MouseDevice
from .base import HardwareBackend, HardwareError
from .capabilities import BatteryState, DpiState, HardwareCapabilities
from .generic import GenericBackend

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class DesiredHardwareState:
    """Safe runtime state reapplied at startup and after every rebind."""

    active_dpi: int = 0
    dpi_stages: tuple[int, ...] = ()
    polling_rate_hz: int | None = None


class HardwareSupervisor(HardwareBackend):
    """Stable backend reference shared by every hardware consumer.

    The supervisor does not poll. A consumer that observes a disconnect asks
    it to rebind; generation checks prevent two consumers from replacing the
    same failed backend twice.
    """

    def __init__(self, backend: HardwareBackend, device: MouseDevice,
                 backend_factory: Callable[[MouseDevice], HardwareBackend],
                 desired: DesiredHardwareState = DesiredHardwareState(),
                 device_resolver: Callable[[MouseDevice], MouseDevice] | None = None,
                 discovery_pending: bool = False) -> None:
        self.device = device
        self._backend = backend
        self._backend_factory = backend_factory
        self.desired = desired
        self._device_resolver = device_resolver or (lambda selected: selected)
        self._discovery_pending = discovery_pending
        # A Generic backend is a safe fallback, but it is not an equivalent
        # replacement for a backend which has already demonstrated native
        # hardware control for this device. Keep probing in that case: hotplug
        # enumeration may expose evdev before the protocol hidraw interface.
        self._has_preferred_backend = not isinstance(backend, GenericBackend)
        self._lock = threading.RLock()
        self._generation = 0
        self._closed = False

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def current_backend(self) -> HardwareBackend:
        with self._lock:
            return self._backend

    @property
    def discovery_pending(self) -> bool:
        with self._lock:
            return self._discovery_pending

    @property
    def name(self) -> str:
        with self._lock:
            return self._backend.name

    def supports_device(self, device: MouseDevice) -> bool:
        return self._call("supports_device", device)

    def _call(self, method: str, *args):
        with self._lock:
            if self._closed:
                raise HardwareError("hardware supervisor is closed")
            if args and isinstance(args[0], MouseDevice):
                args = (self.device, *args[1:])
            return getattr(self._backend, method)(*args)

    @staticmethod
    def _dpi_matches(actual, expected: int) -> bool:
        if isinstance(actual, tuple):
            return actual[0] == expected and actual[1] in (0, expected)
        return actual == expected

    def _reconcile_backend(self, backend: HardwareBackend,
                           device: MouseDevice | None = None) -> None:
        """Apply desired state without changing native control ownership.

        Reconnect/startup reconciliation is automatic lifecycle work, not an
        explicit user takeover. A backend may know how to enter a Host/software
        control mode, but that transition is intentionally ineligible here.
        """
        desired = self.desired
        device = device or self.device
        if desired.polling_rate_hz is not None:
            try:
                if backend.supports_polling_rate_writes_without_takeover(device):
                    backend.set_polling_rate(device, desired.polling_rate_hz)
                    actual = backend.get_polling_rate(device)
                    if actual != desired.polling_rate_hz:
                        raise HardwareError(
                            f"polling verification requested {desired.polling_rate_hz} Hz, "
                            f"read {actual} Hz")
                    LOG.info("Reconciled hardware polling rate to %s Hz through %s",
                             actual, backend.name)
                elif backend.supports_polling_rate(device):
                    LOG.info(
                        "Preserved native control mode; skipped automatic polling write through %s",
                        backend.name,
                    )
            except Exception as exc:
                LOG.warning("Could not reconcile %s polling state: %s", backend.name, exc)

        try:
            if desired.dpi_stages and backend.supports_dpi_stages(device):
                backend.apply_dpi_stages(
                    device, list(desired.dpi_stages), desired.active_dpi)
            if desired.active_dpi > 0 and backend.supports_dpi(device):
                backend.set_dpi(device, desired.active_dpi)
                actual = backend.get_dpi(device)
                if not self._dpi_matches(actual, desired.active_dpi):
                    raise HardwareError(
                        f"DPI verification requested {desired.active_dpi}, read {actual}")
                LOG.info("Reconciled hardware DPI to %s through %s", actual, backend.name)
        except Exception as exc:
            LOG.warning("Could not reconcile %s DPI state: %s", backend.name, exc)

    def reconcile(self) -> None:
        with self._lock:
            if not self._closed:
                self._reconcile_backend(self._backend)

    def record_active_dpi(self, dpi: int | tuple[int, int]) -> None:
        """Keep reconnect reconciliation aligned with confirmed runtime state."""
        value = dpi[0] if isinstance(dpi, tuple) else dpi
        if value <= 0:
            return
        with self._lock:
            if not self._closed:
                self.desired = replace(self.desired, active_dpi=value)

    def rebind(self, expected_generation: int | None = None) -> bool:
        with self._lock:
            if self._closed:
                return False
            if (expected_generation is not None and
                    expected_generation != self._generation):
                return False
            old = self._backend
            replacement = None
            try:
                resolved_device = self._device_resolver(self.device)
                replacement = self._backend_factory(resolved_device)
                self._reconcile_backend(replacement, resolved_device)
            except Exception:
                if replacement is not None:
                    close = getattr(replacement, "close", None)
                    if close:
                        close()
                raise
            if (isinstance(old, GenericBackend) and
                    isinstance(replacement, GenericBackend)):
                # The fallback remains usable while discovery settles. Do not
                # manufacture generations or repeatedly close/reopen it when
                # a probe has not yet found a better backend.
                close = getattr(replacement, "close", None)
                if close:
                    close()
                self.device = resolved_device
                return False
            self.device = resolved_device
            self._backend = replacement
            if not isinstance(replacement, GenericBackend):
                self._has_preferred_backend = True
                self._discovery_pending = False
            elif self._has_preferred_backend:
                # A previously selected native/vendor backend may return after
                # partial hotplug enumeration. Generic is provisional until
                # discovery can safely reacquire that capability.
                self._discovery_pending = True
            self._generation += 1
            if old is not replacement:
                close = getattr(old, "close", None)
                if close:
                    close()
            LOG.info("Rebound hardware backend to %s (generation %d)",
                     replacement.name, self._generation)
            return True

    def get_device_name(self, device): return self._call("get_device_name", device)
    def get_capabilities(self, device) -> HardwareCapabilities: return self._call("get_capabilities", device)
    def supports_battery(self, device): return self._call("supports_battery", device)
    def get_battery_state(self, device) -> BatteryState | None: return self._call("get_battery_state", device)
    def get_dpi_state(self, device) -> DpiState | None: return self._call("get_dpi_state", device)
    def supports_dpi(self, device): return self._call("supports_dpi", device)
    def get_dpi(self, device): return self._call("get_dpi", device)
    def supports_dpi_monitoring(self, device): return self._call("supports_dpi_monitoring", device)
    def supports_dpi_events(self, device): return self._call("supports_dpi_events", device)
    def get_dpi_values(self, device): return self._call("get_dpi_values", device)
    def set_dpi(self, device, dpi): return self._call("set_dpi", device, dpi)
    def supports_dpi_stages(self, device): return self._call("supports_dpi_stages", device)
    def apply_dpi_stages(self, device, stages, active_dpi): return self._call("apply_dpi_stages", device, stages, active_dpi)
    def supports_polling_rate(self, device): return self._call("supports_polling_rate", device)
    def supports_polling_rate_writes(self, device): return self._call("supports_polling_rate_writes", device)
    def supports_polling_rate_writes_without_takeover(self, device): return self._call("supports_polling_rate_writes_without_takeover", device)
    def get_polling_rate(self, device): return self._call("get_polling_rate", device)
    def get_polling_rates(self, device): return self._call("get_polling_rates", device)
    def set_polling_rate(self, device, hz): return self._call("set_polling_rate", device, hz)

    def watch_dpi_events(self, device, callback, shutdown_event,
                         ready_callback=None) -> None:
        # Long-running event reads must not hold the lifecycle lock. Closing a
        # replaced backend wakes the old watcher.
        with self._lock:
            if self._closed:
                raise HardwareError("hardware supervisor is closed")
            backend = self._backend
            device = self.device
        backend.watch_dpi_events(device, callback, shutdown_event, ready_callback)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            close = getattr(self._backend, "close", None)
            if close:
                close()