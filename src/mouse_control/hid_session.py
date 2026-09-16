"""Single-reader HID session with request/reply and event multiplexing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
import logging
from pathlib import Path
import select
import threading

from .hidpp import (DISCOVERY_SOFTWARE_ID, HidppError, HidppReport,
                    parse_hidpp_report)

LOG = logging.getLogger(__name__)


class HidrawIo:
    def __init__(self, path: Path) -> None:
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    def read(self, timeout: float) -> bytes | None:
        readable, _, _ = select.select([self.fd], [], [], timeout)
        return os.read(self.fd, 64) if readable else None

    def write(self, data: bytes) -> None:
        os.write(self.fd, data)

    def close(self) -> None:
        os.close(self.fd)


@dataclass
class _Waiter:
    event: threading.Event
    report: HidppReport | None = None
    error: Exception | None = None


class HidSession:
    """Own all reads for one hidraw interface.

    Requests are serialized. The reader routes matching replies to the current
    waiter and delivers every other valid packet to event subscribers.
    """

    def __init__(self, path: Path, *, io_factory=HidrawIo,
                 timeout: float = 0.75) -> None:
        self.path, self.timeout = path, timeout
        self._io = io_factory(path)
        self._request_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._waiter: tuple[tuple[int, int, int, int], _Waiter] | None = None
        self._callbacks: list[Callable[[HidppReport], None]] = []
        self._callback_failures: dict[Callable[[HidppReport], None], int] = {}
        self._stop = threading.Event()
        self._io_closed = False
        self._thread = threading.Thread(target=self._read_loop,
                                        name="hid-session", daemon=True)
        self._thread.start()

    def subscribe(self, callback: Callable[[HidppReport], None]) -> Callable[[], None]:
        with self._state_lock:
            self._callbacks.append(callback)
        def unsubscribe() -> None:
            with self._state_lock:
                if callback in self._callbacks:
                    self._callbacks.remove(callback)
                self._callback_failures.pop(callback, None)
        return unsubscribe

    @property
    def closed(self) -> bool:
        return self._stop.is_set()

    def request(self, device_index: int, feature_index: int, function: int,
                parameters: bytes = b"") -> HidppReport:
        with self._request_lock:
            key = (device_index, feature_index, function, DISCOVERY_SOFTWARE_ID)
            waiter = _Waiter(threading.Event())
            with self._state_lock:
                if self._stop.is_set():
                    raise HidppError("HID session is closed")
                self._waiter = (key, waiter)
            packet = bytes((0x11, device_index, feature_index,
                            (function << 4) | DISCOVERY_SOFTWARE_ID))
            packet += parameters[:16].ljust(16, b"\0")
            try:
                try:
                    self._io.write(packet)
                except OSError as exc:
                    raise HidppError(f"HID session write failed: {exc}") from exc
                if not waiter.event.wait(self.timeout):
                    raise HidppError("timed out waiting for a matching HID++ response")
                if waiter.error:
                    raise waiter.error
                if waiter.report is None:
                    raise HidppError("HID session closed while waiting for a response")
                return waiter.report
            finally:
                with self._state_lock:
                    if self._waiter and self._waiter[1] is waiter:
                        self._waiter = None

    @staticmethod
    def _error_for(report: HidppReport, key: tuple[int, int, int, int]) -> HidppError | None:
        device, feature, function, software = key
        address = (function << 4) | software
        if (report.device_index == device and report.feature_index == 0xFF and
                ((report.function_or_event << 4) | report.software_id) == feature and
                report.parameters and report.parameters[0] == address):
            code = report.parameters[1] if len(report.parameters) > 1 else 0
            return HidppError(f"device returned HID++ error 0x{code:02x}")
        return None

    def _dispatch(self, report: HidppReport) -> None:
        callbacks: tuple[Callable[[HidppReport], None], ...] = ()
        with self._state_lock:
            if self._waiter:
                key, waiter = self._waiter
                error = self._error_for(report, key)
                if error is not None:
                    waiter.error = error
                    waiter.event.set()
                    return
                if ((report.device_index, report.feature_index,
                     report.function_or_event, report.software_id) == key):
                    waiter.report = report
                    waiter.event.set()
                    return
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            try:
                callback(report)
                self._callback_failures.pop(callback, None)
            except Exception as exc:
                # A consumer cannot kill the sole reader.
                failures = self._callback_failures.get(callback, 0) + 1
                self._callback_failures[callback] = failures
                # Log the first and exponentially spaced repeats. HID++ event
                # handling is low-volume, but a broken subscriber must not
                # flood logs or become invisible.
                if failures & (failures - 1) == 0:
                    LOG.warning("HID event subscriber failed (%d consecutive): %s",
                                failures, exc, exc_info=True)

    def _read_loop(self) -> None:
        failure: Exception | None = None
        try:
            while not self._stop.is_set():
                data = self._io.read(0.25)
                if data == b"":
                    raise OSError("hidraw device disconnected")
                report = parse_hidpp_report(data) if data else None
                if report is not None:
                    self._dispatch(report)
        except Exception as exc:
            failure = exc
        finally:
            with self._state_lock:
                self._stop.set()
                if self._waiter:
                    self._waiter[1].error = HidppError(f"HID session disconnected: {failure}")
                    self._waiter[1].event.set()
            with self._state_lock:
                if not self._io_closed:
                    self._io_closed = True
                    self._io.close()

    def close(self) -> None:
        self._stop.set()
        with self._state_lock:
            if not self._io_closed:
                self._io_closed = True
                self._io.close()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.0)

    def __enter__(self) -> "HidSession":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
