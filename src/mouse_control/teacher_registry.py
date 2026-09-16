"""Native semantic teacher registry for generic protocol learning.

Teachers are answer keys, never protocol implementations. They may expose
confirmed device semantics such as current DPI, polling rate or battery state,
but they cannot expose packet bytes, report offsets, feature indexes, command
IDs or write primitives to the generic learner.

Vendor protocol implementations live behind this boundary. In particular,
Razer teaching uses mouse-control's native Razer protocol reader and has no
runtime dependency on OpenRazer, its daemon, D-Bus service or Python client.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from enum import IntEnum
from typing import Protocol

from .discovery import MouseDevice


TeacherValue = int | bool | str | tuple[int, int] | None


class TeacherTrust(IntEnum):
    """Authority of a teacher's semantic read path."""

    REFERENCE = 1
    READ_VERIFIED = 2
    HARDWARE_VERIFIED = 3
    LOCAL_PROVEN = 4


@dataclass(frozen=True)
class TeacherProvenance:
    """Auditable teacher identity without protocol implementation details."""

    teacher: str
    backend: str
    trust: TeacherTrust
    device_identity: tuple[int | None, int | None, int | None]


@dataclass(frozen=True)
class TeacherState:
    """Protocol-neutral semantic truth supplied to the generic learner.

    The deliberately closed field set is a safety boundary: there is no generic
    metadata bucket in which an adapter could accidentally smuggle packet
    layouts, command IDs or writable protocol details into discovery.
    """

    dpi: tuple[int, int] | None = None
    active_dpi_stage: int | None = None
    polling_rate: int | None = None
    battery: int | None = None
    charging: bool | None = None
    profile: int | str | None = None
    firmware: str | None = None
    model: str | None = None
    provenance: TeacherProvenance | None = None

    def labels(self) -> dict[str, TeacherValue]:
        """Return only semantic labels that are actually known."""

        labels: dict[str, TeacherValue] = {}
        if self.dpi is not None:
            x, y = self.dpi
            labels["dpi"] = x if x == y else (x, y)
        if self.active_dpi_stage is not None:
            labels["dpi_stage"] = int(self.active_dpi_stage)
        if self.polling_rate is not None:
            labels["polling_rate"] = int(self.polling_rate)
        if self.battery is not None:
            labels["battery"] = int(self.battery)
        if self.charging is not None:
            labels["charging"] = bool(self.charging)
        if self.profile is not None:
            labels["profile"] = self.profile
        if self.firmware is not None:
            labels["firmware"] = self.firmware
        if self.model is not None:
            labels["model"] = self.model
        return labels

    @classmethod
    def semantic_field_names(cls) -> frozenset[str]:
        """Expose the intentionally small teacher-to-learner schema."""

        return frozenset(item.name for item in fields(cls) if item.name != "provenance")


class TeacherAdapter(Protocol):
    """One exact-applicability semantic teacher."""

    name: str

    def read_state(self, device: MouseDevice) -> TeacherState | None:
        """Return semantic state when this adapter confidently applies."""


BackendFactory = Callable[[], object]


class _BackendTeacher:
    """Read-only adapter over an already-proven mouse-control backend.

    This remains appropriate for HID++ because mouse-control already owns that
    native protocol backend. Third-party runtime backends must not be used as
    teacher dependencies.
    """

    name = "backend"
    backend_name = "Hardware"
    default_trust = TeacherTrust.READ_VERIFIED
    vendor_ids: frozenset[int] = frozenset()

    def __init__(self, backend_factory: BackendFactory | None = None) -> None:
        self._backend_factory = backend_factory

    def _make_backend(self):
        raise NotImplementedError

    def _trust_for_device(self, device: MouseDevice) -> TeacherTrust:
        return self.default_trust

    @staticmethod
    def _charging_from_status(status: str | None) -> bool | None:
        if status is None:
            return None
        value = status.strip().lower().replace("_", " ").replace("-", " ")
        if value in {"charging", "recharging"}:
            return True
        if value in {"discharging", "not charging", "full", "charged"}:
            return False
        return None

    @staticmethod
    def _safe(call: Callable[[], object], default=None):
        try:
            return call()
        except Exception:
            return default

    def read_state(self, device: MouseDevice) -> TeacherState | None:
        """Read independent semantic facts without ever invoking a write API."""

        if self.vendor_ids and device.vendor not in self.vendor_ids:
            return None

        backend = self._backend_factory() if self._backend_factory is not None else self._make_backend()
        try:
            if not bool(self._safe(lambda: backend.supports_device(device), False)):
                return None

            dpi_state = None
            if bool(self._safe(lambda: backend.supports_dpi_monitoring(device), False)):
                dpi_state = self._safe(lambda: backend.get_dpi_state(device))

            dpi: tuple[int, int] | None = None
            active_stage: int | None = None
            profile: int | str | None = None
            if dpi_state is not None and bool(getattr(dpi_state, "confirmed", False)):
                x = int(dpi_state.x_dpi)
                y_raw = getattr(dpi_state, "y_dpi", None)
                y = x if y_raw in (None, 0) else int(y_raw)
                dpi = (x, y)
                if getattr(dpi_state, "active_stage", None) is not None:
                    active_stage = int(dpi_state.active_stage)
                if getattr(dpi_state, "active_profile", None) is not None:
                    profile = int(dpi_state.active_profile)

            polling_rate = None
            if bool(self._safe(lambda: backend.supports_polling_rate(device), False)):
                value = self._safe(lambda: backend.get_polling_rate(device))
                if value is not None:
                    polling_rate = int(value)

            battery = None
            charging = None
            if bool(self._safe(lambda: backend.supports_battery(device), False)):
                battery_state = self._safe(lambda: backend.get_battery_state(device))
                if battery_state is not None:
                    if getattr(battery_state, "percentage", None) is not None:
                        battery = int(battery_state.percentage)
                    charging = self._charging_from_status(getattr(battery_state, "status", None))

            model = self._safe(lambda: backend.get_device_name(device))
            if model is not None:
                model = str(model)

            state = TeacherState(
                dpi=dpi,
                active_dpi_stage=active_stage,
                polling_rate=polling_rate,
                battery=battery,
                charging=charging,
                profile=profile,
                model=model,
                provenance=TeacherProvenance(
                    teacher=self.name,
                    backend=self.backend_name,
                    trust=self._trust_for_device(device),
                    device_identity=(device.bustype, device.vendor, device.product),
                ),
            )
            return state if state.labels() else None
        finally:
            close = getattr(backend, "close", None)
            if close is not None:
                close()


class HidppTeacher(_BackendTeacher):
    """Semantic teacher backed by mouse-control's native HID++ implementation."""

    name = "hidpp"
    backend_name = "Native HID++"
    vendor_ids = frozenset({0x046D})

    # The G305 is the current locally exercised teacher reference. Other
    # devices successfully bound by the validated HID++ backend remain
    # READ_VERIFIED until mouse-control has equivalent hardware evidence.
    _LOCAL_PROVEN = frozenset({(3, 0x046D, 0x4074)})

    def _make_backend(self):
        from .hardware.native_hid import NativeHidBackend

        return NativeHidBackend()

    def _trust_for_device(self, device: MouseDevice) -> TeacherTrust:
        identity = (device.bustype, device.vendor, device.product)
        if identity in self._LOCAL_PROVEN:
            return TeacherTrust.LOCAL_PROVEN
        return TeacherTrust.READ_VERIFIED


class NativeRazerTeacher:
    """Semantic teacher backed only by mouse-control's native Razer reader."""

    name = "razer"
    backend_name = "Native Razer protocol"
    vendor_ids = frozenset({0x1532})

    def __init__(self, reader: Callable[[MouseDevice], object | None] | None = None) -> None:
        self._reader = reader

    def read_state(self, device: MouseDevice) -> TeacherState | None:
        if device.vendor not in self.vendor_ids:
            return None

        reader = self._reader
        if reader is None:
            from .native_razer import read_native_razer_state

            reader = read_native_razer_state

        semantic = reader(device)
        if semantic is None:
            return None

        state = TeacherState(
            dpi=getattr(semantic, "dpi", None),
            polling_rate=getattr(semantic, "polling_rate", None),
            battery=getattr(semantic, "battery", None),
            charging=getattr(semantic, "charging", None),
            firmware=getattr(semantic, "firmware", None),
            model=getattr(semantic, "model", None),
            provenance=TeacherProvenance(
                teacher=self.name,
                backend=self.backend_name,
                trust=TeacherTrust.READ_VERIFIED,
                device_identity=(device.bustype, device.vendor, device.product),
            ),
        )
        return state if state.labels() else None


class TeacherRegistry:
    """Select the strongest applicable semantic teacher for one mouse."""

    def __init__(self, adapters: Sequence[TeacherAdapter] | None = None) -> None:
        self.adapters: tuple[TeacherAdapter, ...] = tuple(
            adapters if adapters is not None else (HidppTeacher(), NativeRazerTeacher())
        )

    def read_all(self, device: MouseDevice) -> tuple[TeacherState, ...]:
        states: list[TeacherState] = []
        for adapter in self.adapters:
            try:
                state = adapter.read_state(device)
            except (OSError, ImportError, RuntimeError):
                # A teacher is optional evidence. Failure must not turn generic
                # read-only discovery into a hardware-control dependency.
                continue
            if state is None or state.provenance is None or not state.labels():
                continue
            if state.provenance.trust < TeacherTrust.READ_VERIFIED:
                continue
            states.append(state)
        return tuple(states)

    def read_best(self, device: MouseDevice) -> TeacherState | None:
        states = self.read_all(device)
        if not states:
            return None
        # Trust dominates. Semantic coverage breaks ties. Registry order is the
        # final deterministic tie-breaker because Python's max keeps the first.
        return max(
            states,
            key=lambda state: (
                int(state.provenance.trust) if state.provenance is not None else 0,
                len(state.labels()),
            ),
        )


_DEFAULT_TEACHERS = TeacherRegistry()


def read_teacher_state(
    device: MouseDevice,
    *,
    registry: TeacherRegistry | None = None,
) -> TeacherState | None:
    """Return the strongest safely readable semantic teacher state."""

    return (registry or _DEFAULT_TEACHERS).read_best(device)


def read_teacher_labels(
    device: MouseDevice,
    *,
    registry: TeacherRegistry | None = None,
) -> Mapping[str, TeacherValue]:
    """Return only semantic answer-key labels to the generic learner."""

    state = read_teacher_state(device, registry=registry)
    return state.labels() if state is not None else {}
