"""Live mouse remapping using evdev input capture and a virtual uinput device."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import select
import signal
import threading
from typing import Any
import logging

from evdev import InputDevice, UInput, ecodes

from .discovery import MouseDevice, get_mouse_devices
from .hardware import DpiState, HardwareBackend
from .notifications import FreedesktopNotifier, Notifier


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Action:
    kind: str
    code: int | None = None
    codes: tuple[int, ...] = ()


def parse_action(value: str) -> Action:
    value = value.strip()
    if value == "passthrough":
        return Action("passthrough")
    if value == "disable":
        return Action("disable")
    if value == "dpi-cycle":
        return Action("dpi-cycle")
    if value.startswith("mouse:"):
        name = value.split(":", 1)[1]
        code = getattr(ecodes, name, None)
        if code is None or not name.startswith("BTN_"):
            raise ValueError(f"Invalid mouse action: {value}")
        return Action("mouse", code)
    if value.startswith("key:"):
        name = value.split(":", 1)[1]
        code = getattr(ecodes, name, None)
        if code is None or not name.startswith("KEY_"):
            raise ValueError(f"Invalid keyboard action: {value}")
        return Action("key", code)
    if value.startswith("chord:"):
        names = value.split(":", 1)[1].split("+")
        codes = tuple(getattr(ecodes, name, None) for name in names)
        if (len(codes) < 2 or any(not name.startswith("KEY_") or not isinstance(code, int)
                                   for name, code in zip(names, codes))
                or len(set(codes)) != len(codes)):
            raise ValueError(f"Invalid keyboard chord: {value}")
        return Action("chord", codes=codes)
    raise ValueError(f"Unknown action: {value}")


class DpiCycler:
    """Cycle configured stages while retaining the confirmed hardware DPI."""

    def __init__(self, backend: HardwareBackend, device: MouseDevice, stages: list[int],
                 current_dpi: int, notifications_enabled: bool = True,
                 notifier: Notifier | None = None) -> None:
        self.backend = backend
        self.device = device
        self.stages = stages
        self.current_dpi = current_dpi
        self.current_dpi_confirmed = False
        # An off-list starting DPI advances to the first configured stage.
        self._stage_index = stages.index(current_dpi) if current_dpi in stages else -1
        self._cycle_lock = threading.Lock()
        self.notifications_enabled = notifications_enabled
        self.notifier = notifier if notifier is not None else FreedesktopNotifier()

    def cycle(self) -> bool:
        with self._cycle_lock:
            return self._cycle()

    def observe_dpi(self, dpi: int | tuple[int, int]) -> None:
        """Use a confirmed external DPI change to place the software cursor."""
        with self._cycle_lock:
            self.current_dpi = dpi
            self.current_dpi_confirmed = True
            self._stage_index = self.stages.index(dpi) if dpi in self.stages else -1
            self._record_desired_dpi(dpi)

    def _cycle(self) -> bool:
        if not self.stages:
            LOG.warning("DPI cycle ignored: no configured stages")
            return False
        next_index = (self._stage_index + 1) % len(self.stages)
        next_dpi = self.stages[next_index]
        try:
            if not self.backend.supports_dpi(self.device):
                LOG.warning("DPI cycle ignored: %s does not support DPI control", self.backend.name)
                return False
            confirmed = self.backend.set_dpi(self.device, next_dpi)
        except Exception as exc:
            LOG.warning("Could not set DPI to %s through %s: %s",
                        next_dpi, self.backend.name, exc)
            return False
        state_confirmed = isinstance(confirmed, DpiState) and confirmed.confirmed
        actual_dpi = confirmed.display_value if state_confirmed else None
        if actual_dpi is None:
            try:
                readback = self.backend.get_dpi(self.device)
            except Exception as exc:
                LOG.warning("Could not read DPI after cycle through %s: %s",
                            self.backend.name, exc)
                readback = None
            if isinstance(readback, (int, tuple)):
                actual_dpi = readback
                state_confirmed = True
        if actual_dpi is None:
            # Legacy setters without readback remain usable, but are explicitly
            # represented as requested/unconfirmed state.
            actual_dpi = next_dpi
        if state_confirmed and not self._dpi_matches(actual_dpi, next_dpi):
            LOG.warning("DPI cycle verification failed: requested %s, read %s",
                        next_dpi, actual_dpi)
            return False
        self.current_dpi = actual_dpi
        self.current_dpi_confirmed = state_confirmed
        self._stage_index = next_index
        self._record_desired_dpi(actual_dpi)
        if self.notifications_enabled:
            try:
                deliberate = (self.notifier.notify_deliberate_dpi
                              if callable(getattr(type(self.notifier), "notify_deliberate_dpi", None))
                              else None)
                (deliberate or self.notifier.notify_dpi)(actual_dpi)
            except Exception as exc:
                LOG.warning("Desktop DPI notification failed: %s", exc)
        return True

    @staticmethod
    def _dpi_matches(actual_dpi, requested):
        if isinstance(actual_dpi, tuple):
            return actual_dpi[0] == requested and actual_dpi[1] in (0, requested)
        return actual_dpi == requested

    def _record_desired_dpi(self, dpi):
        record = getattr(type(self.backend), "record_active_dpi", None)
        if callable(record):
            record(self.backend, dpi)


class MouseRemapper:
    """Grab one physical mouse and emit translated events through uinput."""

    def __init__(self, device_path: str, mappings: dict[str, str],
                 shutdown_event: threading.Event | None = None,
                 dpi_cycler: DpiCycler | None = None,
                 target_device: MouseDevice | None = None,
                 retry_interval: float = 0.5) -> None:
        self.device_path = device_path
        # An evdev event node is disposable.  Keep the selected mouse's
        # discovery identity separately so a changed eventN can be rebound.
        self.target_device = target_device or MouseDevice("", device_path)
        self.device: InputDevice | None = None
        parsed: dict[int, Action] = {}
        for name, action in mappings.items():
            code = getattr(ecodes, name, None)
            if code is None or not name.startswith("BTN_"):
                raise ValueError(f"Invalid remap source button: {name}")
            parsed[code] = parse_action(action)
        self.mappings = parsed
        self.ui: UInput | None = None
        self.shutdown_event = shutdown_event if shutdown_event is not None else threading.Event()
        self.dpi_cycler = dpi_cycler
        self.retry_interval = retry_interval
        self._pressed_keys: set[int] = set()
        self._held_chords: set[int] = set()
        self._chord_key_counts: dict[int, int] = {}
        self._ambiguity_logged = False

    def _capabilities(self) -> dict[int, list[int]]:
        assert self.device is not None
        caps = self.device.capabilities(verbose=False)
        capabilities: dict[int, list[int]] = {}
        for event_type, codes in caps.items():
            if event_type == ecodes.EV_SYN:
                continue
            capabilities[event_type] = list(codes)

        rel = set(capabilities.get(ecodes.EV_REL, []))
        rel.update((ecodes.REL_X, ecodes.REL_Y))
        capabilities[ecodes.EV_REL] = sorted(rel)

        keys = set(capabilities.get(ecodes.EV_KEY, []))
        keys.update((ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE))
        for action in self.mappings.values():
            if action.code is not None and action.kind in {"mouse", "key"}:
                keys.add(action.code)
            keys.update(action.codes)
        capabilities[ecodes.EV_KEY] = sorted(keys)
        return capabilities

    def _emit(self, event_type: int, code: int, value: int) -> None:
        assert self.ui is not None
        self.ui.write(event_type, code, value)
        if event_type == ecodes.EV_KEY:
            pressed_keys = getattr(self, "_pressed_keys", None)
            if pressed_keys is None:
                pressed_keys = self._pressed_keys = set()
            if value:
                pressed_keys.add(code)
            else:
                pressed_keys.discard(code)

    @staticmethod
    def _is_disconnect(exc: OSError) -> bool:
        return exc.errno in {errno.ENODEV, errno.ENOENT, errno.EIO, errno.ENXIO}

    def _matching_devices(self) -> list[MouseDevice]:
        """Return unambiguous discovery candidates for a non-stable path."""
        target = self.target_device
        if target.vendor is None or target.product is None:
            return []
        matches = [mouse for mouse in get_mouse_devices()
                   if mouse.vendor == target.vendor and mouse.product == target.product
                   and (target.bustype is None or mouse.bustype == target.bustype)]
        if target.phys:
            same_phys = [mouse for mouse in matches if mouse.phys == target.phys]
            if same_phys:
                return same_phys
        # USB topology may change on reconnect. A unique exact device identity
        # is still usable; multiple candidates remain ambiguous.
        return matches

    def _acquire_device(self) -> InputDevice | None:
        # A by-id symlink is the strongest identity we have.  Opening it also
        # follows it when the receiver returns on a different event node.
        if "/dev/input/by-id/" in self.device_path:
            try:
                return InputDevice(self.device_path)
            except OSError as exc:
                if not self._is_disconnect(exc):
                    raise
                return None

        candidates = self._matching_devices()
        if len(candidates) > 1:
            if not self._ambiguity_logged:
                LOG.warning("Mouse reconnect is ambiguous; waiting for the selected device")
                self._ambiguity_logged = True
            return None
        self._ambiguity_logged = False
        path = candidates[0].path if candidates else self.device_path
        try:
            return InputDevice(path)
        except OSError as exc:
            if not self._is_disconnect(exc):
                raise
            return None

    def _release_pressed_keys(self) -> None:
        if self.ui is None:
            self._pressed_keys.clear()
            self._held_chords.clear()
            self._chord_key_counts.clear()
            return
        had_pressed_keys = bool(self._pressed_keys)
        for source in tuple(self._held_chords):
            self._handle_chord(source, self.mappings[source], 0)
        for code in tuple(self._pressed_keys):
            self.ui.write(ecodes.EV_KEY, code, 0)
        if had_pressed_keys:
            self.ui.syn()
        self._pressed_keys.clear()
        self._held_chords.clear()
        self._chord_key_counts.clear()

    def _handle_chord(self, source: int, action: Action, value: int) -> None:
        if value == 1:
            if source in self._held_chords:
                return
            self._held_chords.add(source)
            for key in action.codes:
                count = self._chord_key_counts.get(key, 0)
                if count == 0:
                    self._emit(ecodes.EV_KEY, key, 1)
                self._chord_key_counts[key] = count + 1
        elif value == 0:
            if source not in self._held_chords:
                return
            self._held_chords.remove(source)
            for key in reversed(action.codes):
                count = self._chord_key_counts[key] - 1
                if count:
                    self._chord_key_counts[key] = count
                else:
                    del self._chord_key_counts[key]
                    self._emit(ecodes.EV_KEY, key, 0)

    def _close_device(self) -> None:
        if self.device is None:
            return
        try:
            self.device.ungrab()
        except OSError:
            pass
        self.device.close()
        self.device = None

    def _handle(self, event_type: int, code: int, value: int) -> None:
        if event_type == ecodes.EV_SYN:
            return
        if event_type != ecodes.EV_KEY:
            self._emit(event_type, code, value)
            return

        action = self.mappings.get(code, Action("passthrough"))
        if action.kind == "disable":
            return
        if action.kind == "dpi-cycle":
            if value == 1 and self.dpi_cycler is not None:
                self.dpi_cycler.cycle()
            return
        if action.kind == "passthrough":
            self._emit(event_type, code, value)
        elif action.kind == "chord":
            self._handle_chord(code, action, value)
        elif action.code is not None:
            self._emit(ecodes.EV_KEY, action.code, value)

    def stop(self, *_: Any) -> None:
        self.shutdown_event.set()

    def run(self) -> None:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        try:
            announced = False
            disconnected = False
            while not self.shutdown_event.is_set():
                if self.device is None:
                    self.device = self._acquire_device()
                    if self.device is None:
                        if not disconnected:
                            LOG.warning("Mouse disconnected; waiting for reconnect")
                            disconnected = True
                        self.shutdown_event.wait(self.retry_interval)
                        continue
                    try:
                        self.device.grab()
                    except OSError as exc:
                        self._close_device()
                        if not self._is_disconnect(exc):
                            raise
                        continue
                    if self.ui is None:
                        self.ui = UInput(self._capabilities(),
                                        name=f"mouse-control: {self.device.name}")
                    if disconnected:
                        LOG.info("Mouse reconnected at %s", self.device.path)
                    if not announced:
                        print(f"Remapping: {self.device.name}")
                        print("Press Ctrl+C to stop.")
                        announced = True
                    disconnected = False
                try:
                    readable, _, _ = select.select([self.device.fd], [], [], 0.25)
                    if not readable:
                        continue
                    for event in self.device.read():
                        self._handle(event.type, event.code, event.value)
                        if event.type != ecodes.EV_SYN:
                            self.ui.syn()
                except OSError as exc:
                    if not self._is_disconnect(exc):
                        raise
                    self._release_pressed_keys()
                    self._close_device()
                    disconnected = True
                    LOG.warning("Mouse disconnected; waiting for reconnect")
        finally:
            self._release_pressed_keys()
            self._close_device()
            if self.ui is not None:
                self.ui.close()
                self.ui = None
