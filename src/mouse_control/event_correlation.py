"""Correlate evdev actions, hidraw input reports, and feature-report changes.

This module does not assign vendor semantics. It can establish facts such as
"byte 3 of feature report 5 changes whenever this physical button is pressed";
it cannot turn that observation into writable DPI support.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
import os
from pathlib import Path
import selectors
import time
from typing import Any, Hashable, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class RawStreamIdentity:
    """Path-independent identity for one correlated hidraw input stream."""

    bus: int | None
    vendor_id: int | None
    product_id: int | None
    interface_number: int | None
    descriptor_sha256: str | None

    @property
    def key(self) -> tuple[object, ...]:
        return (
            "input",
            self.bus,
            self.vendor_id,
            self.product_id,
            self.interface_number,
            self.descriptor_sha256,
        )


@dataclass(frozen=True)
class TimedReport:
    timestamp_ns: int
    source: Hashable
    data: bytes
    diagnostic_source: str | None = None


@dataclass(frozen=True)
class TimedEvdevEvent:
    timestamp_ns: int
    source: str
    event_type: int
    code: int
    value: int


@dataclass(frozen=True)
class FeatureChange:
    report_key: Hashable
    offset: int
    before: int | None
    after: int | None


@dataclass
class PhysicalAction:
    start_ns: int
    end_ns: int
    evdev_events: list[TimedEvdevEvent] = field(default_factory=list)
    hid_reports: list[TimedReport] = field(default_factory=list)
    feature_changes: list[FeatureChange] = field(default_factory=list)


@dataclass(frozen=True)
class CorrelationCandidate:
    report_key: Hashable
    offset: int
    observations: int
    values: tuple[int, ...]
    transitions: tuple[tuple[int | None, int | None], ...]
    mirrors: tuple[Hashable, ...] = ()


def diff_feature_snapshots(
    before: Mapping[Hashable, bytes],
    after: Mapping[Hashable, bytes],
) -> list[FeatureChange]:
    """Return byte-level differences between two read-only feature snapshots."""

    changes: list[FeatureChange] = []
    for key in sorted(set(before) | set(after), key=repr):
        old = before.get(key, b"")
        new = after.get(key, b"")
        width = max(len(old), len(new))
        for offset in range(width):
            old_value = old[offset] if offset < len(old) else None
            new_value = new[offset] if offset < len(new) else None
            if old_value != new_value:
                changes.append(FeatureChange(key, offset, old_value, new_value))
    return changes


def suppress_duplicates(
    reports: Iterable[TimedReport],
    *,
    window_ms: float = 40.0,
) -> list[TimedReport]:
    """Suppress identical immediate report echoes while preserving order."""

    if window_ms < 0:
        raise ValueError("window_ms cannot be negative")
    window_ns = int(window_ms * 1_000_000)
    result: list[TimedReport] = []
    last: dict[tuple[Hashable, bytes], int] = {}
    for report in sorted(reports, key=lambda item: item.timestamp_ns):
        key = (report.source, report.data)
        previous = last.get(key)
        if previous is not None and report.timestamp_ns - previous <= window_ns:
            continue
        result.append(report)
        last[key] = report.timestamp_ns
    return result


def correlate_events(
    evdev_events: Sequence[TimedEvdevEvent],
    hid_reports: Sequence[TimedReport],
    *,
    window_ms: float = 100.0,
) -> list[PhysicalAction]:
    """Group HID activity around evdev key-down events.

    If no key-down event exists, the whole capture becomes one observational
    action. This is useful for devices whose DPI control is hidden from evdev.
    """

    if window_ms <= 0:
        raise ValueError("window_ms must be greater than zero")
    window_ns = int(window_ms * 1_000_000)
    clean_reports = suppress_duplicates(hid_reports)
    anchors = [event for event in evdev_events if event.value == 1]
    if not anchors:
        timestamps = [item.timestamp_ns for item in (*evdev_events, *clean_reports)]
        if not timestamps:
            return []
        return [
            PhysicalAction(
                min(timestamps),
                max(timestamps),
                list(evdev_events),
                list(clean_reports),
            )
        ]

    actions: list[PhysicalAction] = []
    for anchor in anchors:
        start = anchor.timestamp_ns - window_ns
        end = anchor.timestamp_ns + window_ns
        actions.append(
            PhysicalAction(
                start_ns=start,
                end_ns=end,
                evdev_events=[e for e in evdev_events if start <= e.timestamp_ns <= end],
                hid_reports=[r for r in clean_reports if start <= r.timestamp_ns <= end],
            )
        )
    return actions


def detect_repeated_changes(
    actions: Sequence[PhysicalAction],
    *,
    minimum_observations: int = 2,
) -> list[CorrelationCandidate]:
    """Find Feature-report bytes that change repeatedly across physical actions."""

    if minimum_observations < 1:
        raise ValueError("minimum_observations must be at least one")
    transitions: dict[tuple[Hashable, int], list[tuple[int | None, int | None]]] = defaultdict(list)
    for action in actions:
        seen_in_action: set[tuple[Hashable, int]] = set()
        for change in action.feature_changes:
            key = (change.report_key, change.offset)
            if key in seen_in_action:
                continue
            transitions[key].append((change.before, change.after))
            seen_in_action.add(key)

    result: list[CorrelationCandidate] = []
    for (report_key, offset), items in transitions.items():
        if len(items) < minimum_observations:
            continue
        values = tuple(
            sorted({value for transition in items for value in transition if value is not None})
        )
        result.append(
            CorrelationCandidate(
                report_key=report_key,
                offset=offset,
                observations=len(items),
                values=values,
                transitions=tuple(items),
            )
        )
    return sorted(result, key=lambda item: (-item.observations, repr(item.report_key), item.offset))


def _report_shape(report: TimedReport) -> Hashable:
    data = bytes(report.data)
    layout = (len(data), data[0] if data else None)
    if isinstance(report.source, RawStreamIdentity):
        return (*report.source.key, *layout)
    # Compatibility for synthetic/older callers. Live guided capture supplies
    # RawStreamIdentity, so volatile paths never enter new semantic hypotheses.
    return (report.source, *layout)


def _shape_layout(shape: Hashable) -> tuple[int, int | None] | None:
    if not isinstance(shape, tuple) or len(shape) < 2:
        return None
    length, report_id = shape[-2:]
    if not isinstance(length, int):
        return None
    if report_id is not None and not isinstance(report_id, int):
        return None
    return length, report_id


def _report_sequences(
    actions: Sequence[PhysicalAction],
) -> dict[Hashable, dict[int, tuple[bytes, ...]]]:
    """Return each raw stream's complete per-action report sequence."""

    result: dict[Hashable, dict[int, tuple[bytes, ...]]] = defaultdict(dict)
    for action_index, action in enumerate(actions):
        grouped: dict[Hashable, list[bytes]] = defaultdict(list)
        for report in action.hid_reports:
            grouped[_report_shape(report)].append(bytes(report.data))
        for shape, reports in grouped.items():
            result[shape][action_index] = tuple(reports)
    return result


def _mirror_aliases(
    actions: Sequence[PhysicalAction],
    *,
    minimum_shared_actions: int = 3,
) -> tuple[dict[Hashable, Hashable], dict[Hashable, tuple[Hashable, ...]]]:
    """Find streams that repeatedly carry the exact same logical reports.

    A mirror requires the same report layout, identical action coverage, and
    identical full report sequences in at least three guided actions. This is
    intentionally stricter than candidate detection: a one-off coincidental
    packet must never merge unrelated interfaces.
    """

    sequences = _report_sequences(actions)
    shapes = sorted(sequences, key=repr)
    parent: dict[Hashable, Hashable] = {shape: shape for shape in shapes}

    def find(item: Hashable) -> Hashable:
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != item:
            next_item = parent[item]
            parent[item] = root
            item = next_item
        return root

    def union(left: Hashable, right: Hashable) -> None:
        a = find(left)
        b = find(right)
        if a == b:
            return
        canonical, other = sorted((a, b), key=repr)
        parent[other] = canonical

    for index, left in enumerate(shapes):
        left_layout = _shape_layout(left)
        if left_layout is None:
            continue
        left_actions = sequences[left]
        for right in shapes[index + 1:]:
            if _shape_layout(right) != left_layout:
                continue
            right_actions = sequences[right]
            if set(left_actions) != set(right_actions):
                continue
            if len(left_actions) < minimum_shared_actions:
                continue
            if all(left_actions[action] == right_actions[action] for action in left_actions):
                union(left, right)

    members: dict[Hashable, list[Hashable]] = defaultdict(list)
    for shape in shapes:
        members[find(shape)].append(shape)

    aliases: dict[Hashable, Hashable] = {}
    mirrors: dict[Hashable, tuple[Hashable, ...]] = {}
    for group in members.values():
        canonical = min(group, key=repr)
        for shape in group:
            aliases[shape] = canonical
        mirrors[canonical] = tuple(sorted((shape for shape in group if shape != canonical), key=repr))
    return aliases, mirrors


def _collapse_mirrored_candidates(
    actions: Sequence[PhysicalAction],
    candidates: Iterable[CorrelationCandidate],
) -> list[CorrelationCandidate]:
    aliases, mirrors = _mirror_aliases(actions)
    result: list[CorrelationCandidate] = []
    seen: set[tuple[Hashable, int, int, tuple[int, ...], tuple[tuple[int | None, int | None], ...]]] = set()
    for candidate in candidates:
        canonical = aliases.get(candidate.report_key, candidate.report_key)
        normalized = replace(
            candidate,
            report_key=canonical,
            mirrors=mirrors.get(canonical, ()),
        )
        fingerprint = (
            normalized.report_key,
            normalized.offset,
            normalized.observations,
            normalized.values,
            normalized.transitions,
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(normalized)
    return sorted(result, key=lambda item: (-item.observations, repr(item.report_key), item.offset))


def detect_repeated_report_fields(
    actions: Sequence[PhysicalAction],
    *,
    minimum_observations: int = 2,
) -> list[CorrelationCandidate]:
    """Find persistent changing byte positions in spontaneous raw HID reports.

    The last report of each logical shape in each guided action is compared
    across actions. Live captures use path-independent stream identities.
    Repeated byte-for-byte mirrored siblings are collapsed only after the raw
    evidence has been retained.
    """

    if minimum_observations < 1:
        raise ValueError("minimum_observations must be at least one")

    per_shape: dict[Hashable, list[tuple[int, bytes]]] = defaultdict(list)
    for action_index, action in enumerate(actions):
        latest: dict[Hashable, bytes] = {}
        for report in action.hid_reports:
            data = bytes(report.data)
            latest[_report_shape(report)] = data
        for shape, data in latest.items():
            per_shape[shape].append((action_index, data))

    raw: list[CorrelationCandidate] = []
    for shape, observed in per_shape.items():
        if len(observed) < minimum_observations:
            continue
        width = min(len(data) for _index, data in observed)
        for offset in range(width):
            ordered_values = tuple(data[offset] for _index, data in observed)
            unique_values = tuple(sorted(set(ordered_values)))
            if len(unique_values) < 2:
                continue
            transitions: list[tuple[int | None, int | None]] = []
            previous: int | None = None
            for value in ordered_values:
                transitions.append((previous, value))
                previous = value
            raw.append(
                CorrelationCandidate(
                    report_key=shape,
                    offset=offset,
                    observations=len(observed),
                    values=unique_values,
                    transitions=tuple(transitions),
                )
            )

    return _collapse_mirrored_candidates(actions, raw)


def detect_repeated_report_transitions(
    actions: Sequence[PhysicalAction],
    *,
    minimum_observations: int = 2,
) -> list[CorrelationCandidate]:
    """Find momentary report-byte transitions repeated inside guided actions.

    Cheap and gaming mice often expose a DPI button as a transient input field:
    ``released -> pressed -> released``. Looking only at the final report makes
    every sample appear identical and loses the useful behavior. This detector
    keeps within-action transitions separate from persistent-state inference so
    a momentary trigger is never mistaken for a DPI-stage register.
    """

    if minimum_observations < 1:
        raise ValueError("minimum_observations must be at least one")

    observed: dict[
        tuple[Hashable, int],
        list[tuple[tuple[int, int], ...]],
    ] = defaultdict(list)

    for action in actions:
        per_shape: dict[Hashable, list[bytes]] = defaultdict(list)
        for report in action.hid_reports:
            per_shape[_report_shape(report)].append(bytes(report.data))

        for shape, reports in per_shape.items():
            if len(reports) < 2:
                continue
            width = min(len(data) for data in reports)
            for offset in range(width):
                sequence = [data[offset] for data in reports]
                changes = tuple(
                    (before, after)
                    for before, after in zip(sequence, sequence[1:])
                    if before != after
                )
                if changes:
                    observed[(shape, offset)].append(changes)

    raw: list[CorrelationCandidate] = []
    for (shape, offset), action_changes in observed.items():
        if len(action_changes) < minimum_observations:
            continue
        flattened = tuple(change for changes in action_changes for change in changes)
        values = tuple(sorted({value for change in flattened for value in change}))
        raw.append(
            CorrelationCandidate(
                report_key=shape,
                offset=offset,
                observations=len(action_changes),
                values=values,
                transitions=flattened,
            )
        )

    return _collapse_mirrored_candidates(actions, raw)


def capture_action_window(
    *,
    evdev_paths: Iterable[str | Path] = (),
    hidraw_paths: Iterable[str | Path] = (),
    hidraw_sources: Mapping[str | Path, Hashable] | None = None,
    seconds: float = 1.0,
    before_features: Mapping[Hashable, bytes] | None = None,
    after_features: Mapping[Hashable, bytes] | None = None,
) -> PhysicalAction:
    """Capture one bounded user action from evdev and hidraw simultaneously.

    ``hidraw_sources`` maps volatile live paths to stable logical identities.
    Paths remain available as diagnostic provenance only. hidraw files are
    opened O_RDONLY|O_NONBLOCK; no grab or HID write operation exists here.
    """

    if seconds <= 0:
        raise ValueError("seconds must be greater than zero")
    try:
        from evdev import InputDevice
    except ImportError as exc:  # pragma: no cover - project dependency
        raise RuntimeError("python-evdev is required for action capture") from exc

    source_map = {
        os.fspath(path): source
        for path, source in (hidraw_sources or {}).items()
    }
    selector = selectors.DefaultSelector()
    evdev_devices: list[Any] = []
    hid_fds: list[int] = []
    events: list[TimedEvdevEvent] = []
    reports: list[TimedReport] = []
    start_ns = time.monotonic_ns()
    deadline = time.monotonic() + seconds

    try:
        for path in evdev_paths:
            device = InputDevice(os.fspath(path))
            evdev_devices.append(device)
            selector.register(device.fd, selectors.EVENT_READ, ("evdev", device))
        for path in hidraw_paths:
            live_path = os.fspath(path)
            fd = os.open(live_path, os.O_RDONLY | os.O_NONBLOCK)
            hid_fds.append(fd)
            logical_source = source_map.get(live_path, live_path)
            selector.register(
                fd,
                selectors.EVENT_READ,
                ("hidraw", logical_source, live_path),
            )

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            for key, _mask in selector.select(timeout=min(remaining, 0.1)):
                kind = key.data[0]
                timestamp = time.monotonic_ns()
                if kind == "hidraw":
                    _kind, logical_source, live_path = key.data
                    try:
                        data = os.read(key.fd, 4096)
                    except BlockingIOError:
                        continue
                    if data:
                        reports.append(
                            TimedReport(
                                timestamp,
                                logical_source,
                                data,
                                diagnostic_source=live_path,
                            )
                        )
                else:
                    _kind, source = key.data
                    try:
                        batch = source.read()
                    except BlockingIOError:
                        continue
                    for event in batch:
                        events.append(
                            TimedEvdevEvent(
                                timestamp_ns=time.monotonic_ns(),
                                source=source.path,
                                event_type=event.type,
                                code=event.code,
                                value=event.value,
                            )
                        )
    finally:
        selector.close()
        for device in evdev_devices:
            device.close()
        for fd in hid_fds:
            os.close(fd)

    action = PhysicalAction(
        start_ns=start_ns,
        end_ns=time.monotonic_ns(),
        evdev_events=events,
        hid_reports=suppress_duplicates(reports),
    )
    if before_features is not None and after_features is not None:
        action.feature_changes = diff_feature_snapshots(before_features, after_features)
    return action
