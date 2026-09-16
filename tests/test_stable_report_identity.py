"""Stable raw-stream identity and mirrored-report regressions."""

from pathlib import Path

from mouse_control.discovery_models import DeviceNode, PhysicalDevice
from mouse_control.event_correlation import (
    PhysicalAction,
    RawStreamIdentity,
    TimedReport,
    detect_repeated_report_fields,
)
from mouse_control.learning_session import LearningSample, ReadOnlyLearningSession
from mouse_control.protocol_grammar import SemanticBehavior


def stream(interface: int, descriptor: str) -> RawStreamIdentity:
    return RawStreamIdentity(3, 0x1234, 0x5678, interface, descriptor)


def report_action(index: int, value: int, *sources: RawStreamIdentity) -> PhysicalAction:
    reports = [
        TimedReport(
            timestamp_ns=index * 100 + offset,
            source=source,
            data=bytes((0x11, 0x00, 0x00, value)),
            diagnostic_source=f"/dev/hidraw{offset + 2}",
        )
        for offset, source in enumerate(sources)
    ]
    return PhysicalAction(index * 100, index * 100 + 20, hid_reports=reports)


def physical() -> PhysicalDevice:
    return PhysicalDevice(
        "Unknown Mouse",
        0x1234,
        0x5678,
        3,
        None,
        model_fingerprint="model",
    )


def test_hidraw_renumbering_does_not_change_logical_report_key():
    identity = stream(2, "abc123")
    first = [
        report_action(1, 1, identity),
        report_action(2, 2, identity),
        report_action(3, 3, identity),
    ]
    for action in first:
        action.hid_reports[0] = TimedReport(
            action.hid_reports[0].timestamp_ns,
            identity,
            action.hid_reports[0].data,
            diagnostic_source="/dev/hidraw2",
        )

    second = [
        report_action(1, 1, identity),
        report_action(2, 2, identity),
        report_action(3, 3, identity),
    ]
    for action in second:
        action.hid_reports[0] = TimedReport(
            action.hid_reports[0].timestamp_ns,
            identity,
            action.hid_reports[0].data,
            diagnostic_source="/dev/hidraw14",
        )

    key_a = next(item for item in detect_repeated_report_fields(first) if item.offset == 3).report_key
    key_b = next(item for item in detect_repeated_report_fields(second) if item.offset == 3).report_key
    assert key_a == key_b
    assert "/dev/hidraw" not in repr(key_a)


def test_repeated_mirrored_siblings_collapse_to_one_candidate():
    left = stream(1, "left")
    right = stream(2, "right")
    actions = [
        report_action(1, 1, left, right),
        report_action(2, 2, left, right),
        report_action(3, 3, left, right),
    ]

    candidates = [item for item in detect_repeated_report_fields(actions) if item.offset == 3]
    assert len(candidates) == 1
    assert len(candidates[0].mirrors) == 1
    assert candidates[0].values == (1, 2, 3)


def test_one_matching_packet_does_not_merge_different_siblings():
    left = stream(1, "left")
    right = stream(2, "right")
    actions = [
        PhysicalAction(
            100,
            120,
            hid_reports=[
                TimedReport(101, left, bytes((0x11, 0, 0, 1))),
                TimedReport(102, right, bytes((0x11, 0, 0, 1))),
            ],
        ),
        PhysicalAction(
            200,
            220,
            hid_reports=[
                TimedReport(201, left, bytes((0x11, 0, 0, 2))),
                TimedReport(202, right, bytes((0x11, 0, 0, 7))),
            ],
        ),
        PhysicalAction(
            300,
            320,
            hid_reports=[
                TimedReport(301, left, bytes((0x11, 0, 0, 3))),
                TimedReport(302, right, bytes((0x11, 0, 0, 8))),
            ],
        ),
    ]

    candidates = [item for item in detect_repeated_report_fields(actions) if item.offset == 3]
    assert len(candidates) == 2
    assert all(candidate.mirrors == () for candidate in candidates)


def test_teacher_hypothesis_uses_stable_report_identity_only():
    identity = stream(2, "abc123")
    samples = [
        LearningSample(report_action(1, 1, identity), {"dpi": 800}),
        LearningSample(report_action(2, 2, identity), {"dpi": 1500}),
        LearningSample(report_action(3, 3, identity), {"dpi": 2000}),
    ]
    learned = ReadOnlyLearningSession(physical(), {}).analyze(
        samples,
        trigger_behavior=SemanticBehavior.DPI_CYCLE_TRIGGER,
    )

    hypothesis = next(
        item
        for item in learned.hypotheses
        if item.behavior is SemanticBehavior.DPI_VALUE and item.offset == 3
    )
    assert dict(hypothesis.mapping or {}) == {1: 800, 2: 1500, 3: 2000}
    assert "/dev/hidraw" not in repr(hypothesis.report_key)
    assert "/dev/input/event" not in repr(hypothesis.report_key)


def test_learning_session_maps_live_path_to_stable_interface_identity():
    node = DeviceNode(
        path=Path("/dev/hidraw14"),
        sysfs_path=None,
        subsystem="hidraw",
        node_type="hidraw",
        bus=3,
        vendor_id=0x1234,
        product_id=0x5678,
        interface_number=4,
        descriptor_sha256="deadbeef",
    )
    device = physical()
    device.hidraw_nodes = [node]
    session = ReadOnlyLearningSession(device, {})

    mapping = session._hidraw_source_map((Path("/dev/hidraw14"),))
    identity = mapping["/dev/hidraw14"]
    assert identity == RawStreamIdentity(3, 0x1234, 0x5678, 4, "deadbeef")
    assert "/dev/hidraw14" not in repr(identity)
