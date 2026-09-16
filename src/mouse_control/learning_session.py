"""Guided, read-only behavioral learning for unknown mouse protocols.

A learning session observes repeated user actions across *all readable*
correlated evdev and hidraw interfaces, takes safe Feature-report snapshots
before/after each action, and asks semantic inference to identify repeated
fields. It never contains a HID write operation.

Known native teachers may optionally provide semantic ground truth before and
after each action. That ground truth can validate behavior without teaching the
learner any vendor-specific packet offsets or command IDs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Callable, Mapping

from .discovery_models import DeviceNode, PhysicalDevice
from .event_correlation import (
    CorrelationCandidate,
    PhysicalAction,
    RawStreamIdentity,
    capture_action_window,
    detect_repeated_changes,
    detect_repeated_report_fields,
    detect_repeated_report_transitions,
    diff_feature_snapshots,
)
from .hid_descriptor import ParsedHidDescriptor
from .hid_probe import ReadOnlyHidProbe
from .protocol_grammar import SemanticBehavior
from .semantic_inference import (
    SemanticHypothesis,
    infer_stage_hypotheses,
    infer_trigger_hypotheses,
)


TeacherReader = Callable[[], Mapping[str, object]]


@dataclass(frozen=True)
class LearningSample:
    action: PhysicalAction
    teacher_state: Mapping[str, object] = field(default_factory=dict)
    unreadable_hidraw_paths: tuple[str, ...] = ()
    teacher_before_state: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class LearningResult:
    samples: tuple[LearningSample, ...]
    feature_candidates: tuple[CorrelationCandidate, ...]
    report_candidates: tuple[CorrelationCandidate, ...]
    trigger_candidates: tuple[CorrelationCandidate, ...]
    hypotheses: tuple[SemanticHypothesis, ...]


class ReadOnlyLearningSession:
    """Observe one physical mouse without issuing any unknown write."""

    def __init__(
        self,
        physical: PhysicalDevice,
        descriptors: Mapping[DeviceNode, ParsedHidDescriptor],
        *,
        probe_factory: Callable[[DeviceNode], ReadOnlyHidProbe] | None = None,
    ) -> None:
        self.physical = physical
        self.descriptors = dict(descriptors)
        self._probe_factory = probe_factory or (
            lambda node: ReadOnlyHidProbe(node.path, sysfs_path=node.sysfs_path)
        )

    @staticmethod
    def _feature_key(node: DeviceNode, report_id: int) -> tuple[object, ...]:
        # No live path: this key is safe to persist if a future profile stores
        # an inferred field location.
        return (
            "feature",
            node.interface_number,
            node.descriptor_sha256,
            report_id,
        )

    @staticmethod
    def _raw_stream_identity(node: DeviceNode) -> RawStreamIdentity:
        """Return the stable identity used by raw input-report inference."""

        return RawStreamIdentity(
            bus=node.bus,
            vendor_id=node.vendor_id,
            product_id=node.product_id,
            interface_number=node.interface_number,
            descriptor_sha256=node.descriptor_sha256,
        )

    @staticmethod
    def _teacher_dpi_value(state: Mapping[str, object]) -> int | None:
        """Extract a protocol-neutral scalar DPI label from teacher state."""

        raw = state.get("dpi")
        if isinstance(raw, tuple) and raw:
            return int(raw[0])
        if isinstance(raw, int) and not isinstance(raw, bool):
            return raw
        return None

    def snapshot_features(self) -> dict[tuple[object, ...], bytes]:
        """Read every descriptor-declared Feature report that permits GET_FEATURE."""

        result: dict[tuple[object, ...], bytes] = {}
        for node, descriptor in self.descriptors.items():
            if not descriptor.feature_reports:
                continue
            probe = self._probe_factory(node)
            try:
                snapshot = probe.snapshot_feature_reports(descriptor.feature_reports)
            except (OSError, PermissionError):
                continue
            for report_id, data in snapshot.items():
                result[self._feature_key(node, report_id)] = bytes(data)
        return result

    def _readable_hidraw_paths(self) -> tuple[tuple[Path, ...], tuple[str, ...]]:
        """Return hidraw nodes usable for passive capture plus skipped nodes.

        Composite mice routinely expose sibling HID interfaces with different
        kernel/udev permissions. One unreadable sibling is missing evidence,
        not a reason to abort learning from every other readable interface.
        The probe is O_RDONLY|O_NONBLOCK and closes immediately; no report is
        written and no input is consumed here.
        """

        readable: list[Path] = []
        skipped: list[str] = []
        for node in self.physical.hidraw_nodes:
            path = Path(node.path)
            try:
                fd = os.open(os.fspath(path), os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                skipped.append(os.fspath(path))
                continue
            try:
                readable.append(path)
            finally:
                os.close(fd)
        return tuple(readable), tuple(skipped)

    def _hidraw_source_map(
        self,
        readable_paths: tuple[Path, ...],
    ) -> dict[str, RawStreamIdentity]:
        """Map volatile live paths to stable logical stream identities."""

        readable = {os.fspath(path) for path in readable_paths}
        result: dict[str, RawStreamIdentity] = {}
        for node in self.physical.hidraw_nodes:
            path = os.fspath(node.path)
            if path in readable:
                result[path] = self._raw_stream_identity(node)
        return result

    def hidraw_access_report(self) -> tuple[tuple[Path, ...], tuple[str, ...]]:
        """Report passive capture access for every correlated hidraw sibling.

        Full-evidence discovery uses this as a preflight invariant: privilege is
        allowed to increase visibility, but the learner must never silently
        claim a complete capture when one of the selected mouse's HID siblings
        could not be opened.
        """

        return self._readable_hidraw_paths()

    def observe_action(
        self,
        *,
        seconds: float = 1.5,
        teacher_reader: TeacherReader | None = None,
        teacher_before_state: Mapping[str, object] | None = None,
    ) -> LearningSample:
        """Capture one bounded physical action plus before/after semantic state.

        ``teacher_before_state`` is intentionally supplied by the caller so a
        native-teacher query can happen *before* the user prompt. This keeps the
        teacher's protocol traffic outside the blind raw-capture window.
        """

        before = self.snapshot_features()
        readable_hidraw, unreadable_hidraw = self._readable_hidraw_paths()
        action = capture_action_window(
            evdev_paths=(node.path for node in self.physical.evdev_nodes),
            hidraw_paths=readable_hidraw,
            hidraw_sources=self._hidraw_source_map(readable_hidraw),
            seconds=seconds,
        )
        after = self.snapshot_features()
        action.feature_changes = diff_feature_snapshots(before, after)
        teacher_state = dict(teacher_reader()) if teacher_reader is not None else {}
        return LearningSample(
            action=action,
            teacher_state=teacher_state,
            unreadable_hidraw_paths=unreadable_hidraw,
            teacher_before_state=dict(teacher_before_state or {}),
        )

    def analyze(
        self,
        samples: list[LearningSample] | tuple[LearningSample, ...],
        *,
        trigger_behavior: SemanticBehavior | None = None,
    ) -> LearningResult:
        actions = tuple(sample.action for sample in samples)
        feature_candidates = tuple(detect_repeated_changes(actions))
        report_candidates = tuple(detect_repeated_report_fields(actions))
        trigger_candidates = tuple(detect_repeated_report_transitions(actions))

        stage_hypotheses = [
            *infer_stage_hypotheses(feature_candidates),
            *infer_stage_hypotheses(report_candidates),
        ]
        teacher_hypotheses = self._teacher_dpi_hypotheses(
            samples, (*feature_candidates, *report_candidates)
        )
        trigger_hypotheses = (
            infer_trigger_hypotheses(trigger_candidates, behavior=trigger_behavior)
            if trigger_behavior is not None
            else ()
        )
        teacher_trigger_hypotheses = (
            self._teacher_dpi_trigger_hypotheses(
                samples,
                trigger_candidates,
                behavior=trigger_behavior,
            )
            if trigger_behavior is not None
            else ()
        )

        # Deduplicate equivalent semantic locations while preserving the more
        # useful teacher-validated hypothesis when both paths found the same field.
        merged: dict[tuple[object, object, object], SemanticHypothesis] = {}
        for hypothesis in (
            *stage_hypotheses,
            *teacher_hypotheses,
            *trigger_hypotheses,
            *teacher_trigger_hypotheses,
        ):
            key = (hypothesis.behavior, repr(hypothesis.report_key), hypothesis.offset)
            previous = merged.get(key)
            if previous is None or (
                previous.confidence == "correlated" and hypothesis.confidence == "validated"
            ):
                merged[key] = hypothesis

        return LearningResult(
            samples=tuple(samples),
            feature_candidates=feature_candidates,
            report_candidates=report_candidates,
            trigger_candidates=trigger_candidates,
            hypotheses=tuple(merged.values()),
        )

    @classmethod
    def _teacher_dpi_hypotheses(
        cls,
        samples: list[LearningSample] | tuple[LearningSample, ...],
        candidates: tuple[CorrelationCandidate, ...],
    ) -> tuple[SemanticHypothesis, ...]:
        """Label persistent raw states when a proven teacher supplies current DPI.

        This does not prove that writing the candidate field changes DPI. It
        only validates a *read-side* raw-state -> DPI relationship. Momentary
        trigger candidates are deliberately excluded from this mapping.
        """

        teacher_dpi: list[int] = []
        for sample in samples:
            value = cls._teacher_dpi_value(sample.teacher_state)
            if value is None:
                return ()
            teacher_dpi.append(value)

        result: list[SemanticHypothesis] = []
        for candidate in candidates:
            states = [after for _before, after in candidate.transitions if after is not None]
            if len(states) != len(teacher_dpi):
                continue
            mapping: dict[int, int] = {}
            conflict = False
            for raw, dpi in zip(states, teacher_dpi):
                previous = mapping.get(raw)
                if previous is not None and previous != dpi:
                    conflict = True
                    break
                mapping[raw] = dpi
            if conflict or len(mapping) < 2:
                continue
            result.append(
                SemanticHypothesis(
                    behavior=SemanticBehavior.DPI_VALUE,
                    confidence="validated",
                    reason=(
                        "persistent raw state consistently mapped to teacher-confirmed current DPI "
                        f"across {len(teacher_dpi)} guided actions"
                    ),
                    report_key=candidate.report_key,
                    offset=candidate.offset,
                    mapping=mapping,
                )
            )
        return tuple(result)

    @classmethod
    def _teacher_dpi_trigger_hypotheses(
        cls,
        samples: list[LearningSample] | tuple[LearningSample, ...],
        candidates: tuple[CorrelationCandidate, ...],
        *,
        behavior: SemanticBehavior,
    ) -> tuple[SemanticHypothesis, ...]:
        """Validate the semantic action without claiming a unique raw byte.

        The native teacher independently proves that the guided physical action
        changed DPI. Raw report candidates remain correlated evidence underneath
        that semantic event until additional observations establish which field
        or report is the actual protocol encoding. This separation prevents a
        teacher from turning every co-changing byte into a supposedly proven
        packet location.
        """

        transitions: list[tuple[int, int]] = []
        for sample in samples:
            before = cls._teacher_dpi_value(sample.teacher_before_state)
            after = cls._teacher_dpi_value(sample.teacher_state)
            if before is None or after is None:
                continue
            transitions.append((before, after))

        if (
            not candidates
            or len(transitions) < 2
            or any(before == after for before, after in transitions)
        ):
            return ()

        return (
            SemanticHypothesis(
                behavior=behavior,
                confidence="validated",
                reason=(
                    f"guided action produced {len(candidates)} correlated raw trigger candidate(s) "
                    f"while the native teacher independently confirmed DPI changed in "
                    f"{len(transitions)} before/after action pairs; raw locations remain candidates"
                ),
            ),
        )
