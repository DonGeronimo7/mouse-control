"""Teacher-registry isolation and semantic-read regressions."""

from dataclasses import fields

from mouse_control.backend_teacher import (
    HidppTeacher,
    NativeRazerTeacher,
    TeacherProvenance,
    TeacherRegistry,
    TeacherState,
    TeacherTrust,
    read_backend_teacher_state,
)
from mouse_control.discovery import MouseDevice
from mouse_control.hardware.capabilities import BatteryState, DpiState
from mouse_control.native_razer import NativeRazerState


class FakeBackend:
    def __init__(self) -> None:
        self.closed = False
        self.write_calls = 0

    def supports_device(self, _device):
        return True

    def supports_dpi_monitoring(self, _device):
        return True

    def get_dpi_state(self, _device):
        return DpiState(1600, 1600, active_stage=2, active_profile=1, confirmed=True)

    def supports_polling_rate(self, _device):
        return True

    def get_polling_rate(self, _device):
        return 1000

    def supports_battery(self, _device):
        return True

    def get_battery_state(self, _device):
        return BatteryState(percentage=90, status="charging")

    def get_device_name(self, _device):
        return "Teacher Mouse"

    def set_dpi(self, *_args, **_kwargs):
        self.write_calls += 1
        raise AssertionError("teacher attempted a DPI write")

    def set_polling_rate(self, *_args, **_kwargs):
        self.write_calls += 1
        raise AssertionError("teacher attempted a polling-rate write")

    def close(self):
        self.closed = True


def g305():
    return MouseDevice(
        "Logitech G305",
        "/dev/input/event13",
        vendor=0x046D,
        product=0x4074,
        bustype=3,
    )


def razer():
    return MouseDevice(
        "Razer Viper V3 Pro",
        "/dev/input/event20",
        vendor=0x1532,
        product=0x00C1,
        bustype=3,
    )


def test_hidpp_teacher_reads_semantics_only_and_marks_local_proven():
    backend = FakeBackend()
    state = HidppTeacher(backend_factory=lambda: backend).read_state(g305())

    assert state is not None
    assert state.labels() == {
        "dpi": 1600,
        "dpi_stage": 2,
        "polling_rate": 1000,
        "battery": 90,
        "charging": True,
        "profile": 1,
        "model": "Teacher Mouse",
    }
    assert state.provenance is not None
    assert state.provenance.trust is TeacherTrust.LOCAL_PROVEN
    assert backend.closed is True
    assert backend.write_calls == 0


def test_native_razer_teacher_uses_native_reader_semantics_only():
    calls = []

    def reader(device):
        calls.append(device)
        return NativeRazerState(
            model="Viper V3 Pro",
            firmware="1.12",
            dpi=(1600, 1600),
            polling_rate=1000,
            battery=90,
            charging=False,
        )

    state = NativeRazerTeacher(reader=reader).read_state(razer())

    assert calls == [razer()]
    assert state is not None
    assert state.labels() == {
        "dpi": 1600,
        "polling_rate": 1000,
        "battery": 90,
        "charging": False,
        "firmware": "1.12",
        "model": "Viper V3 Pro",
    }
    assert state.provenance is not None
    assert state.provenance.teacher == "razer"
    assert state.provenance.backend == "Native Razer protocol"
    assert state.provenance.trust is TeacherTrust.READ_VERIFIED


def test_native_razer_teacher_rejects_non_razer_before_reader():
    calls = []
    state = NativeRazerTeacher(reader=lambda _device: calls.append(True)).read_state(g305())
    assert state is None
    assert calls == []


def test_teacher_state_has_no_protocol_escape_hatch():
    field_names = {item.name for item in fields(TeacherState)}
    forbidden_fragments = {
        "packet",
        "report",
        "offset",
        "command",
        "feature",
        "checksum",
        "write",
        "payload",
        "raw",
        "metadata",
    }
    assert all(
        fragment not in name
        for name in field_names
        for fragment in forbidden_fragments
    )
    assert TeacherState.semantic_field_names() == {
        "dpi",
        "active_dpi_stage",
        "polling_rate",
        "battery",
        "charging",
        "profile",
        "firmware",
        "model",
    }


class StaticTeacher:
    def __init__(self, name, trust, labels):
        self.name = name
        self.trust = trust
        self._labels = labels

    def read_state(self, device):
        return TeacherState(
            dpi=self._labels.get("dpi"),
            polling_rate=self._labels.get("polling_rate"),
            provenance=TeacherProvenance(
                teacher=self.name,
                backend=self.name,
                trust=self.trust,
                device_identity=(device.bustype, device.vendor, device.product),
            ),
        )


def test_registry_prefers_authority_before_semantic_coverage():
    weaker = StaticTeacher(
        "weaker",
        TeacherTrust.READ_VERIFIED,
        {"dpi": (800, 800), "polling_rate": 1000},
    )
    stronger = StaticTeacher(
        "stronger",
        TeacherTrust.HARDWARE_VERIFIED,
        {"dpi": (1600, 1600)},
    )
    state = TeacherRegistry((weaker, stronger)).read_best(g305())
    assert state is not None
    assert state.provenance is not None
    assert state.provenance.teacher == "stronger"
    assert state.labels()["dpi"] == 1600


def test_reference_only_teacher_is_not_accepted_as_ground_truth():
    reference = StaticTeacher(
        "reference",
        TeacherTrust.REFERENCE,
        {"dpi": (1600, 1600)},
    )
    assert TeacherRegistry((reference,)).read_best(g305()) is None


def test_compatibility_bridge_returns_only_semantic_labels():
    teacher = StaticTeacher(
        "local",
        TeacherTrust.LOCAL_PROVEN,
        {"dpi": (1600, 1600), "polling_rate": 1000},
    )
    labels = read_backend_teacher_state(g305(), registry=TeacherRegistry((teacher,)))
    assert labels == {"dpi": 1600, "polling_rate": 1000}
    assert all(not isinstance(value, (bytes, bytearray)) for value in labels.values())
