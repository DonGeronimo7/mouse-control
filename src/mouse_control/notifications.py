"""Optional DPI monitoring and Freedesktop desktop notifications."""
from __future__ import annotations
import asyncio, logging, queue, threading
from collections.abc import Callable
from enum import Enum, auto
from typing import Protocol
from .discovery import MouseDevice
from .hardware import HardwareBackend
from .hardware.capabilities import DpiState

LOG = logging.getLogger(__name__)

class MonitorState(Enum):
    UNBOUND = auto(); BINDING = auto(); READY = auto(); DISCONNECTED = auto(); STOPPING = auto()

class Notifier(Protocol):
    def notify_dpi(self, dpi: int | tuple[int, int]) -> None: ...
    def start(self) -> None: ...

class FreedesktopNotifier:
    """Send independent short-lived OSDs; DBus failures never affect HID."""
    _CONNECT = object()
    def __init__(self) -> None:
        self._queue: queue.Queue[object] = queue.Queue(); self._thread = None
        self._start_lock = threading.Lock(); self._ready = threading.Event()
    @staticmethod
    def _body(dpi):
        if isinstance(dpi, tuple):
            return f"{dpi[0]} DPI" if dpi[1] in (0, dpi[0]) else f"{dpi[0]} × {dpi[1]} DPI"
        return f"{dpi} DPI"
    async def _connect(self):
        from dbus_next import BusType
        from dbus_next.aio import MessageBus
        return await MessageBus(bus_type=BusType.SESSION).connect()
    async def _notify(self, bus, dpi):
        from dbus_next import Message, Variant
        from dbus_next.constants import MessageType
        reply = await bus.call(Message(destination="org.freedesktop.Notifications", path="/org/freedesktop/Notifications", interface="org.freedesktop.Notifications", member="Notify", signature="susssasa{sv}i", body=["mouse-control", 0, "", "Mouse DPI", self._body(dpi), [], {"urgency": Variant("y", 1), "transient": Variant("b", True), "suppress-sound": Variant("b", True)}, 1500]))
        if reply.message_type == MessageType.ERROR: raise RuntimeError(reply.body[0] if reply.body else reply.error_name)
        return int(reply.body[0])
    async def _run_async(self):
        bus = None
        try:
            while True:
                try: request = self._queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(.05); continue
                try:
                    if request is None: return
                    if bus is None: bus = await self._connect(); self._ready.set()
                    if request is not self._CONNECT:
                        LOG.info("DPI Notify: %s replaces_id=0", self._body(request)); await self._notify(bus, request)
                except Exception as exc:
                    LOG.warning("DPI notification failed: %s", exc); self._ready.clear()
                    if bus:
                        try: bus.disconnect()
                        except Exception: pass
                    bus = None
                finally: self._queue.task_done()
        finally:
            self._ready.clear()
            if bus:
                try: bus.disconnect()
                except Exception: pass
    def start(self):
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=lambda: asyncio.run(self._run_async()), name="dpi-notifier", daemon=True); self._thread.start(); self._queue.put(self._CONNECT)
    def is_ready(self): return self._ready.is_set()
    def notify_dpi(self, dpi): self.start(); self._queue.put(dpi)
    def wait_idle(self): self._queue.join()
    def close(self):
        if self._thread and self._thread.is_alive(): self._queue.put(None); self._thread.join(timeout=1)

class DpiMonitor:
    def __init__(self, backend, device, notifier=None, interval=1., shutdown_event=None):
        self.backend, self.device, self.notifier, self.interval = backend, device, notifier or FreedesktopNotifier(), interval
        self.shutdown_event = shutdown_event or threading.Event(); self._last_dpi = None; self._read_failed = self._notify_failed = False; self._thread = None
    def poll_once(self):
        try:
            dpi = self.backend.get_dpi(self.device)
            if dpi is None: return
            self._read_failed = False
        except Exception as exc:
            if not self._read_failed: LOG.warning("DPI monitoring read failed; will retry: %s", exc); self._read_failed = True
            return
        previous, self._last_dpi = self._last_dpi, dpi
        if previous is None or dpi == previous: return
        try: self.notifier.notify_dpi(dpi); self._notify_failed = False
        except Exception as exc:
            if not self._notify_failed: LOG.warning("Desktop DPI notification failed; will retry: %s", exc); self._notify_failed = True
    def _run(self):
        self.poll_once()
        while not self.shutdown_event.wait(self.interval): self.poll_once()
    def start(self): self._thread = threading.Thread(target=self._run, name="dpi-monitor", daemon=True); self._thread.start()
    def stop(self):
        self.shutdown_event.set()
        if self._thread: self._thread.join(timeout=max(1., self.interval+.5))
        if close := getattr(self.notifier, "close", None): close()

class DpiEventMonitor:
    def __init__(self, backend, device, stages, active_dpi, notifier=None, shutdown_event=None, log_errors=True, dpi_cycler=None):
        self.backend, self.device, self.notifier = backend, device, notifier or FreedesktopNotifier(); self.shutdown_event = shutdown_event or threading.Event()
        self._last_notified_dpi = None; self._state_lock = threading.Lock(); self._thread = None; self.log_errors = log_errors
        self.dpi_cycler = dpi_cycler
        self._last_stage = None
    def notify_dpi(self, dpi):
        with self._state_lock:
            if dpi == self._last_notified_dpi: return False
            try: self.notifier.notify_dpi(dpi)
            except Exception as exc: LOG.warning("Desktop DPI notification failed: %s", exc); return False
            self._last_notified_dpi = dpi
        return True
    def notify_deliberate_dpi(self, dpi):
        with self._state_lock:
            try: self.notifier.notify_dpi(dpi)
            except Exception as exc: LOG.warning("Desktop DPI notification failed: %s", exc); return False
            self._last_notified_dpi = dpi
        return True
    def handle_state(self, state):
        if not state.confirmed or state.x_dpi <= 0: LOG.warning("Ignoring unconfirmed hardware DPI state"); return
        if self.dpi_cycler is not None and state.active_stage is not None:
            # A physical press advances the onboard slot. Repeated reports for
            # that slot, including our live-write echo, are one transition.
            if state.active_stage == self._last_stage:
                return
            self._last_stage = state.active_stage
            self.dpi_cycler.backend = self.backend
            self.dpi_cycler.cycle()
            return
        if self.dpi_cycler is not None and state.active_stage is None:
            self.dpi_cycler.observe_dpi(state.display_value)
        if self.notify_dpi(state.display_value): LOG.info("Hardware DPI changed to %s (stage %s)", state.display_value, state.active_stage)
    def _run(self, ready_callback=None):
        try: self.backend.watch_dpi_events(self.device, self.handle_state, self.shutdown_event, ready_callback)
        except Exception as exc:
            if self.log_errors:
                LOG.warning("HID++ DPI monitoring stopped: %s", exc)
            else:
                raise
    def start(self): self._thread = threading.Thread(target=self._run, name="dpi-event-monitor", daemon=True); self._thread.start()
    def stop(self):
        self.shutdown_event.set()
        if self._thread: self._thread.join(timeout=1)
        if close := getattr(self.notifier, "close", None): close()

class DpiMonitorSupervisor:
    """Freshly discover a backend after every unavailable or failed watcher."""
    def __init__(self, backend, device, backend_factory, stages, active_dpi, shutdown_event, notifier=None, retry_interval=1., dpi_cycler=None, notifications_enabled=True):
        self.backend, self.device, self.backend_factory, self.stages = backend, device, backend_factory, stages; self.shutdown_event = shutdown_event; self.notifier = notifier or FreedesktopNotifier(); self.retry_interval = retry_interval
        self._last_notified_dpi = None; self._state_lock = threading.Lock(); self._thread = None; self._watcher_bound = False; self._state = MonitorState.UNBOUND
        self.dpi_cycler = dpi_cycler
        self.notifications_enabled = notifications_enabled
    def notify_dpi(self, dpi):
        if not self.notifications_enabled: return False
        with self._state_lock:
            if dpi == self._last_notified_dpi: return False
            try: self.notifier.notify_dpi(dpi)
            except Exception as exc: LOG.warning("Desktop DPI notification failed: %s", exc); return False
            self._last_notified_dpi = dpi
        return True
    def notify_deliberate_dpi(self, dpi):
        if not self.notifications_enabled: return False
        with self._state_lock:
            try: self.notifier.notify_dpi(dpi)
            except Exception as exc: LOG.warning("Desktop DPI notification failed: %s", exc); return False
            self._last_notified_dpi = dpi
        return True
    def _watcher_ready(self):
        with self._state_lock: self._last_notified_dpi = None; self._watcher_bound = True; self._state = MonitorState.READY
        LOG.info("DPI event watcher ready; DPI monitor ready")
    def _run(self):
        backend, unavailable = self.backend, False
        while not self.shutdown_event.is_set():
            generation = getattr(backend, "generation", None)
            unsupported = False
            try:
                if start := getattr(self.notifier, "start", None): start()
                with self._state_lock: self._watcher_bound = False; self._state = MonitorState.BINDING
                monitor = create_dpi_monitor(backend, self.device, shutdown_event=self.shutdown_event, stages=self.stages, notifier=self, log_failure=False, dpi_cycler=self.dpi_cycler)
                if monitor is None:
                    if not unavailable: LOG.warning("DPI monitoring unavailable; retrying")
                    unavailable = True
                    unsupported = not getattr(backend, "discovery_pending", True)
                else:
                    monitor._run(self._watcher_ready)
                    if not self.shutdown_event.is_set():
                        with self._state_lock: self._state = MonitorState.DISCONNECTED
                        if not unavailable: LOG.warning("DPI monitor stopped; retrying")
                        unavailable = True
            except Exception as exc:
                if not unavailable: LOG.warning("DPI monitoring unavailable; retrying: %s", exc)
                unavailable = True
            if unsupported:
                if self.shutdown_event.wait(max(30., self.retry_interval)):
                    break
                continue
            if self.shutdown_event.is_set() or self.shutdown_event.wait(self.retry_interval): break
            try:
                rebind = getattr(type(backend), "rebind", None)
                if callable(rebind):
                    rebind(backend, generation)
                else:
                    if close := getattr(backend, "close", None): close()
                    backend = self.backend_factory(self.device)
            except Exception as exc: LOG.debug("DPI backend discovery unavailable: %s", exc)
    def start(self): self._thread = threading.Thread(target=self._run, name="dpi-monitor-supervisor", daemon=True); self._thread.start()
    def stop(self):
        with self._state_lock: self._state = MonitorState.STOPPING
        self.shutdown_event.set()
        if self._thread: self._thread.join(timeout=max(1., self.retry_interval+.5))
        if close := getattr(self.notifier, "close", None): close()

def create_dpi_monitor(backend, device, enabled=True, shutdown_event=None, stages=None, active_dpi=0, notifier=None, log_failure=True, dpi_cycler=None):
    if not enabled: return None
    try:
        if backend.supports_dpi_events(device) is True: return DpiEventMonitor(backend, device, stages or [], active_dpi, notifier, shutdown_event, log_failure, dpi_cycler)
        if not backend.supports_dpi_monitoring(device): return None
    except Exception as exc:
        if log_failure: LOG.warning("DPI monitoring is unavailable: %s", exc)
        return None
    return DpiMonitor(backend, device, notifier, shutdown_event=shutdown_event)
