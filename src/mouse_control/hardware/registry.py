"""Ordered factories; register future backends here without changing callers."""
import logging
from collections.abc import Callable, Iterable
from .base import HardwareBackend, HardwareError
from .generic import GenericBackend
from .openrazer import OpenRazerBackend
from .native_hid import NativeHidBackend
from ..discovery import MouseDevice

BACKEND_FACTORIES = (NativeHidBackend, OpenRazerBackend)


def get_backend(device: MouseDevice, factories: Iterable[Callable[[], HardwareBackend]] | None = None,
                *, log_failures: bool = True) -> HardwareBackend:
    for factory in BACKEND_FACTORIES if factories is None else factories:
        backend = None
        try:
            backend = factory()
            if backend.supports_device(device):
                return backend
        except (HardwareError, OSError) as exc:
            if log_failures:
                logging.getLogger(__name__).warning("Hardware discovery failed: %s", exc)
        if backend is not None:
            close = getattr(backend, "close", None)
            if close:
                close()
    return GenericBackend()
