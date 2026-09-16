"""Read-only hidraw inspection primitives for unknown hardware discovery.

This module intentionally exposes no SET_REPORT, HIDIOCSFEATURE,
HIDIOCSOUTPUT, or raw-write API.  Known protocol drivers own writes after
protocol semantics have been proven; generic discovery can only inspect.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import select
import struct
import time
from typing import Callable, Iterable

from .discovery_models import HidReportDefinition


HID_MAX_DESCRIPTOR_SIZE = 4096

# Linux _IOC encoding.  Kept local to avoid requiring an extra C extension.
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
_IOC_WRITE = 1
_IOC_READ = 2


def _IOC(direction: int, ioctl_type: int, nr: int, size: int) -> int:
    return (
        (direction << _IOC_DIRSHIFT)
        | (ioctl_type << _IOC_TYPESHIFT)
        | (nr << _IOC_NRSHIFT)
        | (size << _IOC_SIZESHIFT)
    )


def _IOR(ioctl_type: int, nr: int, size: int) -> int:
    return _IOC(_IOC_READ, ioctl_type, nr, size)


_H = ord("H")
_HIDIOCGRDESCSIZE = _IOR(_H, 0x01, struct.calcsize("i"))
_HIDIOCGRDESC = _IOR(_H, 0x02, 4 + HID_MAX_DESCRIPTOR_SIZE)
_HIDIOCGRAWINFO = _IOR(_H, 0x03, struct.calcsize("Ihh"))


def _HIDIOCGFEATURE(length: int) -> int:
    # GET_FEATURE is encoded READ|WRITE because byte zero supplies the report
    # ID to the kernel; this is still a device read and does not issue SET_REPORT.
    return _IOC(_IOC_READ | _IOC_WRITE, _H, 0x07, length)


@dataclass(frozen=True)
class HidRawInfo:
    bus: int
    vendor_id: int
    product_id: int
    name: str = ""
    phys: str = ""
    uniq: str = ""


class ReadOnlyHidProbe:
    """Safe inspection handle for one hidraw interface."""

    def __init__(self, path: str | Path, *, sysfs_path: Path | None = None) -> None:
        self.path = Path(path)
        self.sysfs_path = sysfs_path or Path("/sys/class/hidraw") / self.path.name / "device"

    def _open(self) -> int:
        return os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)

    def read_descriptor(self) -> bytes:
        """Return the raw report descriptor without modifying device state."""

        descriptor_path = self.sysfs_path / "report_descriptor"
        try:
            raw = descriptor_path.read_bytes()
            if raw:
                return raw
        except OSError:
            pass

        fd = self._open()
        try:
            size_buffer = bytearray(struct.calcsize("i"))
            fcntl.ioctl(fd, _HIDIOCGRDESCSIZE, size_buffer, True)
            size = struct.unpack("i", size_buffer)[0]
            if not 0 < size <= HID_MAX_DESCRIPTOR_SIZE:
                raise OSError(f"invalid HID descriptor size {size}")
            buffer = bytearray(4 + HID_MAX_DESCRIPTOR_SIZE)
            struct.pack_into("I", buffer, 0, size)
            fcntl.ioctl(fd, _HIDIOCGRDESC, buffer, True)
            returned_size = struct.unpack_from("I", buffer, 0)[0]
            if not 0 < returned_size <= HID_MAX_DESCRIPTOR_SIZE:
                raise OSError(f"invalid returned HID descriptor size {returned_size}")
            return bytes(buffer[4:4 + returned_size])
        finally:
            os.close(fd)

    def read_raw_info(self) -> HidRawInfo:
        """Read bus/VID/PID and descriptive sysfs identity."""

        fd = self._open()
        try:
            buffer = bytearray(struct.calcsize("Ihh"))
            fcntl.ioctl(fd, _HIDIOCGRAWINFO, buffer, True)
            bus, vendor, product = struct.unpack("Ihh", buffer)
        finally:
            os.close(fd)

        fields: dict[str, str] = {}
        try:
            for line in (self.sysfs_path / "uevent").read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    fields[key] = value
        except OSError:
            pass
        return HidRawInfo(
            bus=bus,
            vendor_id=vendor & 0xFFFF,
            product_id=product & 0xFFFF,
            name=fields.get("HID_NAME", ""),
            phys=fields.get("HID_PHYS", ""),
            uniq=fields.get("HID_UNIQ", ""),
        )

    def capture_input_reports(
        self,
        seconds: float,
        callback: Callable[[bytes], None] | None = None,
    ) -> list[bytes]:
        """Capture raw input reports for a bounded window using O_RDONLY."""

        if seconds <= 0:
            raise ValueError("capture duration must be greater than zero")
        reports: list[bytes] = []
        fd = self._open()
        try:
            deadline = time.monotonic() + seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                readable, _, _ = select.select([fd], [], [], min(remaining, 0.25))
                if not readable:
                    continue
                try:
                    report = os.read(fd, 4096)
                except BlockingIOError:
                    continue
                if not report:
                    continue
                reports.append(report)
                if callback is not None:
                    callback(report)
        finally:
            os.close(fd)
        return reports

    def get_feature_report(self, report_id: int, length: int) -> bytes:
        """Issue HIDIOCGFEATURE only; no corresponding SET API exists here."""

        if not 0 <= report_id <= 0xFF:
            raise ValueError("report_id must fit in one byte")
        if not 1 <= length < (1 << _IOC_SIZEBITS):
            raise ValueError("invalid feature-report buffer length")

        buffer = bytearray(length)
        buffer[0] = report_id
        fd = self._open()
        try:
            count = fcntl.ioctl(fd, _HIDIOCGFEATURE(length), buffer, True)
        finally:
            os.close(fd)

        if isinstance(count, int) and 0 < count <= len(buffer):
            return bytes(buffer[:count])
        return bytes(buffer)

    def snapshot_feature_reports(
        self,
        reports: Iterable[HidReportDefinition],
        *,
        strict: bool = False,
    ) -> dict[int, bytes]:
        """Read every descriptor-declared Feature report that the device allows."""

        snapshot: dict[int, bytes] = {}
        for report in reports:
            if report.report_type != "feature":
                continue
            # hidraw feature ioctls reserve byte zero for report ID even when the
            # descriptor uses unnumbered report 0.
            length = report.byte_length if report.report_id else report.byte_length + 1
            length = max(length, 1)
            try:
                snapshot[report.report_id] = self.get_feature_report(
                    report.report_id, length
                )
            except (OSError, PermissionError):
                if strict:
                    raise
        return snapshot
