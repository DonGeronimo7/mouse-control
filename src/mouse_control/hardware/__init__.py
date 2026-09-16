"""Public hardware capability API.

Core capability models and the supervisor are safe to import without loading
vendor backends.  Backend registry loading is intentionally lazy: importing a
protocol driver may itself need ``hardware.capabilities``, and eagerly loading
``registry`` here would recurse through ``native_hid`` back into that partially
initialized protocol module.
"""

from __future__ import annotations

from .base import HardwareBackend, HardwareError
from .capabilities import (
    BatteryCapabilities,
    BatteryState,
    DpiCapabilities,
    DpiRange,
    DpiState,
    HardwareCapabilities,
    ReportRateCapabilities,
)
from .supervisor import DesiredHardwareState, HardwareSupervisor

__all__ = [
    "BatteryCapabilities",
    "BatteryState",
    "DpiCapabilities",
    "DpiRange",
    "DpiState",
    "HardwareBackend",
    "HardwareCapabilities",
    "HardwareError",
    "ReportRateCapabilities",
    "DesiredHardwareState",
    "HardwareSupervisor",
    "get_backend",
]


def __getattr__(name: str):
    """Lazily expose backend selection without creating protocol import cycles."""

    if name == "get_backend":
        from .registry import get_backend

        # Cache the resolved export so normal runtime callers pay the lazy
        # import cost only once.
        globals()[name] = get_backend
        return get_backend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
