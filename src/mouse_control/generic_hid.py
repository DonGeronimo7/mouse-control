"""Read-only generic HID discovery used to develop native device protocols.

USB HID does not standardize mouse DPI or report-rate configuration.  This
module therefore discovers and captures reports without issuing feature or
output reports.  Validated vendor protocols can consume the resulting device
identity through hardware backends without weakening the evdev fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import select
import time
from typing import Callable

from .discovery import MouseDevice


@dataclass(frozen=True)
class GenericHidDevice:
    path: Path
    vendor_id: int
    product_id: int
    name: str
    phys: str
    report_descriptor: bytes


def discover_hid_devices(device: MouseDevice, *,
                         sysfs: Path = Path("/sys/class/hidraw"),
                         dev_root: Path = Path("/dev")) -> list[GenericHidDevice]:
    """Return hidraw interfaces matching an evdev mouse's exact VID/PID.

    Multiple interfaces are intentionally retained: inexpensive wireless mice
    commonly expose separate pointer and vendor-defined interfaces.  The bus
    type is included so the same dual-mode mouse can be inspected over USB or
    Bluetooth without treating those transports as interchangeable.
    """
    if device.vendor is None or device.product is None or device.bustype is None:
        return []
    expected = f"{device.bustype:04X}:{device.vendor:08X}:{device.product:08X}"
    matches: list[GenericHidDevice] = []
    for entry in sorted(sysfs.glob("hidraw*")):
        node = entry / "device"
        try:
            fields = dict(line.split("=", 1) for line in
                          (node / "uevent").read_text().splitlines() if "=" in line)
            if fields.get("HID_ID") != expected:
                continue
            descriptor = (node / "report_descriptor").read_bytes()
        except OSError:
            continue
        matches.append(GenericHidDevice(
            dev_root / entry.name,
            device.vendor,
            device.product,
            fields.get("HID_NAME", device.name),
            fields.get("HID_PHYS", ""),
            descriptor,
        ))
    return matches


def capture_input_reports(hid_device: GenericHidDevice, seconds: float,
                          callback: Callable[[bytes], None]) -> None:
    """Capture raw input reports read-only for a bounded diagnostic window."""
    if seconds <= 0:
        raise ValueError("Capture duration must be greater than zero")
    fd = os.open(hid_device.path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            readable, _, _ = select.select([fd], [], [], min(remaining, 0.25))
            if readable:
                report = os.read(fd, 4096)
                if report:
                    callback(report)
    finally:
        os.close(fd)
