"""Capability contract. Backends translate library failures to HardwareError.

Empty value lists mean enumeration is unavailable, not a guessed range.
Unsupported reads return None/[]; unsupported writes raise HardwareError.
Instances are scoped to one selection and must not guess ambiguous identities.
"""
from abc import ABC, abstractmethod
import threading
from typing import Callable
from ..discovery import MouseDevice
from .capabilities import BatteryState, DpiState, HardwareCapabilities


class HardwareError(RuntimeError):
    """Hardware operation failed; software remapping can continue."""


class HardwareBackend(ABC):
    name = "Hardware"

    @abstractmethod
    def supports_device(self, device: MouseDevice) -> bool:
        """Probe and bind a confidently matched physical device."""

    def get_device_name(self, device: MouseDevice) -> str | None:
        return device.name

    def get_capabilities(self, device: MouseDevice) -> HardwareCapabilities:
        return HardwareCapabilities()

    def supports_battery(self, device: MouseDevice) -> bool:
        return self.get_capabilities(device).battery.readable

    def get_battery_state(self, device: MouseDevice) -> BatteryState | None:
        return None

    def get_dpi_state(self, device: MouseDevice) -> DpiState | None:
        dpi = self.get_dpi(device)
        if dpi is None:
            return None
        if isinstance(dpi, tuple):
            return DpiState(int(dpi[0]), int(dpi[1]), confirmed=True)
        return DpiState(int(dpi), int(dpi), confirmed=True)

    def supports_dpi(self, device: MouseDevice) -> bool:
        return False

    def get_dpi(self, device: MouseDevice) -> int | tuple[int, int] | None:
        return None

    def supports_dpi_monitoring(self, device: MouseDevice) -> bool:
        """Return whether repeated current-DPI reads are supported."""
        return False

    def supports_dpi_events(self, device: MouseDevice) -> bool:
        return False

    def watch_dpi_events(self, device: MouseDevice, callback: Callable[[DpiState], None],
                         shutdown_event: threading.Event,
                         ready_callback: Callable[[], None] | None = None) -> None:
        """Watch DPI events and call ``ready_callback`` after subscribing."""
        raise HardwareError(f"{self.name}: DPI events are unsupported")

    def get_dpi_values(self, device: MouseDevice) -> list[int]:
        return []

    def set_dpi(self, device: MouseDevice, dpi: int) -> DpiState | None:
        raise HardwareError(f"{self.name}: DPI is unsupported")

    def supports_dpi_stages(self, device: MouseDevice) -> bool:
        return False

    def apply_dpi_stages(self, device: MouseDevice, stages: list[int], active_dpi: int) -> int | None:
        """Return selected slot, or None if the active DPI could not be placed."""
        return None

    def supports_polling_rate(self, device: MouseDevice) -> bool:
        """Return whether polling/report-rate reads are supported."""
        return False

    def supports_polling_rate_writes(self, device: MouseDevice) -> bool:
        """Return whether a proven execution path can write the report rate.

        This may include an explicit control-mode takeover. Discovery and
        automatic lifecycle code must use
        :meth:`supports_polling_rate_writes_without_takeover` instead.
        """
        return False

    def supports_polling_rate_writes_without_takeover(self, device: MouseDevice) -> bool:
        """Return whether polling can be written without changing control ownership.

        The default is equivalent to ordinary write support. Backends with a
        native/onboard versus host/software mode split must override this and
        return False while a write would require taking ownership from firmware.
        """
        return self.supports_polling_rate_writes(device)

    def get_polling_rate(self, device: MouseDevice) -> int | None:
        return None

    def get_polling_rates(self, device: MouseDevice) -> list[int]:
        return []

    def set_polling_rate(self, device: MouseDevice, hz: int) -> None:
        raise HardwareError(f"{self.name}: polling rate is unsupported")

    def close(self) -> None:
        """Release backend resources. Implementations may safely call twice."""
        return None