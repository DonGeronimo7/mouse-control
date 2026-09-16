"""Optional OpenRazer Python/session-D-Bus adapter; no lighting or profiles."""
from functools import wraps
from .base import HardwareBackend, HardwareError
from .capabilities import DpiState
from ..discovery import MouseDevice


def _library_errors(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except HardwareError:
            raise
        except Exception as exc:
            # OpenRazer, its optional imports and D-Bus expose several exception
            # families. Translate at this library boundary, never silently ignore.
            raise HardwareError(f"OpenRazer {method.__name__}: {exc}") from exc
    return wrapped


class OpenRazerBackend(HardwareBackend):
    name = "OpenRazer"

    def __init__(self, manager_factory=None) -> None:
        self._manager_factory = manager_factory
        self._manager = None
        self._devices = {}

    @_library_errors
    def supports_device(self, device: MouseDevice) -> bool:
        if device in self._devices:
            return True
        if device.vendor != 0x1532 or device.product is None:
            return False
        if self._manager is None:
            factory = self._manager_factory
            if factory is None:
                from openrazer.client import DeviceManager
                factory = DeviceManager
            self._manager = factory()
        # The upstream client caches getVidPid() as _vid/_pid and currently has
        # no public VID/PID properties. Keep this compatibility detail here.
        matches = [d for d in self._manager.devices
                   if d.type == "mouse" and int(d._vid) == device.vendor
                   and int(d._pid) == device.product]
        if len(matches) > 1:
            raise HardwareError("OpenRazer: ambiguous USB VID/PID; refusing hardware writes")
        if not matches:
            return False
        self._devices[device] = matches[0]
        return True

    def _device(self, device):
        if not self.supports_device(device):
            raise HardwareError("No matching OpenRazer mouse")
        return self._devices[device]

    @_library_errors
    def get_device_name(self, device: MouseDevice) -> str | None:
        return str(self._device(device).name)

    @_library_errors
    def supports_dpi(self, device: MouseDevice) -> bool:
        return bool(self._device(device).has("dpi"))

    @_library_errors
    def get_dpi(self, device: MouseDevice) -> tuple[int, int] | None:
        if not self.supports_dpi(device):
            return None
        x, y = self._device(device).dpi
        return int(x), int(y)

    def supports_dpi_monitoring(self, device: MouseDevice) -> bool:
        return self.supports_dpi(device)

    @_library_errors
    def get_dpi_values(self, device: MouseDevice) -> list[int]:
        target = self._device(device)
        return [int(v) for v in target.available_dpi] if target.has("available_dpi") else []

    @_library_errors
    def set_dpi(self, device: MouseDevice, dpi: int) -> DpiState:
        target = self._device(device)
        if not self.supports_dpi(device):
            raise HardwareError("OpenRazer: DPI is unsupported")
        values = self.get_dpi_values(device)
        if dpi <= 0 or dpi > target.max_dpi or (values and dpi not in values):
            raise HardwareError(f"OpenRazer: unsupported DPI {dpi}")
        target.dpi = (dpi, 0 if target.has("available_dpi") else dpi)
        actual = self.get_dpi(device)
        if actual is None or actual[0] != dpi or actual[1] not in (0, dpi):
            raise HardwareError(f"OpenRazer: DPI verification failed: requested {dpi}, read {actual}")
        return DpiState(actual[0], actual[1], confirmed=True)

    @_library_errors
    def supports_polling_rate(self, device: MouseDevice) -> bool:
        return bool(self._device(device).has("poll_rate"))

    def supports_polling_rate_writes(self, device: MouseDevice) -> bool:
        return self.supports_polling_rate(device)

    @_library_errors
    def get_polling_rate(self, device: MouseDevice) -> int | None:
        return int(self._device(device).poll_rate) if self.supports_polling_rate(device) else None

    @_library_errors
    def get_polling_rates(self, device: MouseDevice) -> list[int]:
        target = self._device(device)
        # Older daemons cannot enumerate rates. Do not assume 1000 Hz support.
        return [int(v) for v in target.supported_poll_rates] if target.has("supported_poll_rates") else []

    @_library_errors
    def set_polling_rate(self, device: MouseDevice, hz: int) -> int:
        if not self.supports_polling_rate(device):
            raise HardwareError("OpenRazer: polling rate is unsupported")
        rates = self.get_polling_rates(device)
        if hz <= 0 or (rates and hz not in rates):
            raise HardwareError(f"OpenRazer: unsupported polling rate {hz}")
        self._device(device).poll_rate = hz
        actual = self.get_polling_rate(device)
        if actual != hz:
            raise HardwareError(
                f"OpenRazer: polling-rate verification failed: requested {hz}, read {actual}")
        return actual

    def close(self) -> None:
        self._devices.clear()
        manager, self._manager = self._manager, None
        close = getattr(manager, "close", None)
        if close:
            close()
