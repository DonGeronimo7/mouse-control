"""Regression tests for native-teacher semantic transition validation."""

from mouse_control.discovery_models import PhysicalDevice
from mouse_control.event_correlation import PhysicalAction, TimedReport
from mouse_control.learning_session import LearningSample, ReadOnlyLearningSession
from mouse_control.protocol_grammar import SemanticBehavior


def _session() -> ReadOnlyLearningSession:
    physical = PhysicalDevice(
        "Test Mouse",
        0x1234,
        0x5678,
        3,
        None,
        model_fingerprint="model",
    )
    return ReadOnlyLearningSession(physical, {})


def _trigger_action(timestamp: int, *, final_state: int = 0) -> PhysicalAction:
    return PhysicalAction(
        timestamp,
        timestamp + 3,
        hid_reports=[
            TimedReport(timestamp, "stable-stream", bytes((0x02, 0, 0, 0))),
            TimedReport(timestamp + 1, "stable-stream", bytes((0x02, 0, 0, 1))),
            TimedReport(timestamp + 2, "stable-stream", bytes((0x02, 0, 0, final_state))),
        ],
    )


def test_teacher_validates_semantic_event_without_claiming_raw_byte():
    session = _session()
    samples = [
        LearningSample(
            _trigger_action(10),
            {"dpi": 1500},
            teacher_before_state={"dpi": 800},
        ),
        LearningSample(
            _trigger_action(20),
            {"dpi": 2000},
            teacher_before_state={"dpi": 1500},
        ),
        LearningSample(
            _trigger_action(30),
            {"dpi": 2500},
            teacher_before_state={"dpi": 2000},
        ),
    ]

    learned = session.analyze(
        samples,
        trigger_behavior=SemanticBehavior.DPI_CYCLE_TRIGGER,
    )

    semantic_event = next(
        hypothesis
        for hypothesis in learned.hypotheses
        if hypothesis.behavior is SemanticBehavior.DPI_CYCLE_TRIGGER
        and hypothesis.confidence == "validated"
    )
    assert semantic_event.report_key is None
    assert semantic_event.offset is None
    assert "raw locations remain candidates" in semantic_event.reason

    raw_candidate = next(
        hypothesis
        for hypothesis in learned.hypotheses
        if hypothesis.behavior is SemanticBehavior.DPI_CYCLE_TRIGGER
        and hypothesis.offset == 3
    )
    assert raw_candidate.confidence == "correlated"


def test_teacher_does_not_validate_trigger_when_dpi_did_not_change():
    session = _session()
    samples = [
        LearningSample(
            _trigger_action(10),
            {"dpi": 800},
            teacher_before_state={"dpi": 800},
        ),
        LearningSample(
            _trigger_action(20),
            {"dpi": 800},
            teacher_before_state={"dpi": 800},
        ),
    ]

    learned = session.analyze(
        samples,
        trigger_behavior=SemanticBehavior.DPI_CYCLE_TRIGGER,
    )

    assert not any(
        hypothesis.behavior is SemanticBehavior.DPI_CYCLE_TRIGGER
        and hypothesis.confidence == "validated"
        for hypothesis in learned.hypotheses
    )
    raw_candidate = next(
        hypothesis
        for hypothesis in learned.hypotheses
        if hypothesis.behavior is SemanticBehavior.DPI_CYCLE_TRIGGER
        and hypothesis.offset == 3
    )
    assert raw_candidate.confidence == "correlated"


def test_teacher_does_not_invent_raw_dpi_mapping_from_two_state_event_field():
    session = _session()
    samples = [
        LearningSample(
            _trigger_action(10, final_state=0),
            {"dpi": 800},
            teacher_before_state={"dpi": 3000},
        ),
        LearningSample(
            _trigger_action(20, final_state=1),
            {"dpi": 1500},
            teacher_before_state={"dpi": 800},
        ),
        LearningSample(
            _trigger_action(30, final_state=0),
            {"dpi": 2000},
            teacher_before_state={"dpi": 1500},
        ),
    ]

    learned = session.analyze(
        samples,
        trigger_behavior=SemanticBehavior.DPI_CYCLE_TRIGGER,
    )

    assert not any(
        hypothesis.behavior is SemanticBehavior.DPI_VALUE
        for hypothesis in learned.hypotheses
    )
