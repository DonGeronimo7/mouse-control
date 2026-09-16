"""Regression tests for momentary guided-action discovery."""

from mouse_control.discovery_models import PhysicalDevice
from mouse_control.event_correlation import (
    PhysicalAction,
    TimedReport,
    detect_repeated_report_fields,
    detect_repeated_report_transitions,
)
from mouse_control.learning_session import LearningSample, ReadOnlyLearningSession
from mouse_control.protocol_grammar import SemanticBehavior


def action(start: int) -> PhysicalAction:
    return PhysicalAction(
        start,
        start + 10,
        hid_reports=[
            TimedReport(start + 1, "/dev/hidraw8", bytes((0x11, 0x00, 0x00, 0x01))),
            TimedReport(start + 2, "/dev/hidraw8", bytes((0x11, 0x00, 0x00, 0x00))),
        ],
    )


def physical() -> PhysicalDevice:
    return PhysicalDevice(
        "Unknown Mouse", 0x1234, 0x5678, 3, None, model_fingerprint="model"
    )


def test_momentary_press_release_is_not_misclassified_as_persistent_state():
    actions = [action(10), action(20), action(30)]
    assert detect_repeated_report_fields(actions) == []

    candidates = detect_repeated_report_transitions(actions)
    candidate = next(item for item in candidates if item.offset == 3)
    assert candidate.observations == 3
    assert candidate.values == (0, 1)
    assert candidate.transitions == ((1, 0), (1, 0), (1, 0))


def test_guided_dpi_action_labels_repeated_momentary_field_as_trigger_only():
    session = ReadOnlyLearningSession(physical(), {})
    samples = [LearningSample(action(10)), LearningSample(action(20)), LearningSample(action(30))]
    learned = session.analyze(
        samples,
        trigger_behavior=SemanticBehavior.DPI_CYCLE_TRIGGER,
    )

    assert learned.report_candidates == ()
    assert len(learned.trigger_candidates) >= 1
    assert any(
        hypothesis.behavior is SemanticBehavior.DPI_CYCLE_TRIGGER
        and hypothesis.offset == 3
        and hypothesis.confidence == "correlated"
        for hypothesis in learned.hypotheses
    )
    assert not any(
        hypothesis.behavior is SemanticBehavior.DPI_STAGE_INDEX
        for hypothesis in learned.hypotheses
    )
