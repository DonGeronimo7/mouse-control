"""Dedicated CLI for automatic-hardware-discovery acceptance and learning.

Keeping this command separate from the normal setup/runtime path lets us test
real hardware without silently changing the behavior of ``mouse-control run``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .device_topology import TopologyError
from .discovery import get_mouse_devices, select_mouse_device
from .discovery_engine import DiscoveryEngine
from .discovery_ui import render_discovery_result, result_to_dict
from .learning_session import ReadOnlyLearningSession
from .protocol_grammar import SemanticBehavior
from .teacher_registry import read_teacher_labels


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mouse-control-discover",
        description=(
            "Safety-first automatic mouse hardware discovery. Known protocol "
            "queries are validated; unknown HID inspection remains read-only."
        ),
    )
    parser.add_argument(
        "--device",
        type=int,
        metavar="N",
        help="select mouse N from the detected list instead of prompting",
    )
    parser.add_argument("--verbose", action="store_true", help="show discovery evidence")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="do not cache the path-independent discovery profile",
    )
    parser.add_argument(
        "--generic-only",
        action="store_true",
        help=(
            "skip known protocol detectors and exercise only topology, descriptor, "
            "repertoire and read-only learning paths"
        ),
    )
    parser.add_argument(
        "--full-access",
        action="store_true",
        help=(
            "require root hardware visibility and a complete passive-open preflight "
            "for every hidraw sibling correlated to the selected physical mouse; "
            "unknown HID writes remain forbidden"
        ),
    )
    parser.add_argument(
        "--learn-dpi-button",
        action="store_true",
        help=(
            "after discovery, capture three read-only DPI-button actions and infer "
            "both persistent state and momentary trigger fields"
        ),
    )
    parser.add_argument(
        "--teacher",
        action="store_true",
        help=(
            "during guided learning, read semantic ground truth before and after each "
            "action from a native protocol teacher such as HID++; packet details are not shared"
        ),
    )
    parser.add_argument(
        "--learn-window",
        type=float,
        default=1.5,
        metavar="SECONDS",
        help="capture window for each guided action (default: 1.5)",
    )
    return parser


def _pick_mouse(index: int | None):
    mice = get_mouse_devices()
    if not mice:
        print("No mouse devices found. Check input permissions.", file=sys.stderr)
        return None, 1
    if index is None:
        return select_mouse_device(mice), 0
    if index < 1 or index > len(mice):
        print(f"--device must be between 1 and {len(mice)}", file=sys.stderr)
        return None, 2
    return mice[index - 1], 0


def _render_guided_learning(learned) -> str:
    lines = [
        "",
        "Guided DPI-button learning",
        "==========================",
        f"Samples: {len(learned.samples)}",
        f"Feature-field candidates: {len(learned.feature_candidates)}",
        f"Persistent raw-state candidates: {len(learned.report_candidates)}",
        f"Momentary raw-trigger candidates: {len(learned.trigger_candidates)}",
    ]

    if learned.hypotheses:
        lines.append("Semantic hypotheses:")
        for hypothesis in learned.hypotheses:
            location = (
                f" report={hypothesis.report_key!r} byte={hypothesis.offset}"
                if hypothesis.report_key is not None
                else ""
            )
            mapping = f" mapping={dict(hypothesis.mapping)}" if hypothesis.mapping else ""
            lines.append(
                f"  {hypothesis.confidence:10} {hypothesis.behavior.value}{location}{mapping}"
            )
            lines.append(f"               {hypothesis.reason}")
    else:
        lines.append(
            "Semantic hypotheses: none yet — no repeatable persistent or momentary field was found."
        )

    teacher_transitions = [
        {
            "before": dict(sample.teacher_before_state),
            "after": dict(sample.teacher_state),
        }
        for sample in learned.samples
        if sample.teacher_before_state or sample.teacher_state
    ]
    if teacher_transitions:
        lines.append(f"Teacher transitions: {teacher_transitions}")
    lines.append(
        "Write status: forbidden — guided learning produces observations/correlations only."
    )
    return "\n".join(lines)


def _full_access_preflight(session: ReadOnlyLearningSession) -> tuple[str, ...]:
    readable, unreadable = session.hidraw_access_report()
    if unreadable:
        raise PermissionError(
            "full-access discovery requires every correlated hidraw sibling to be readable; "
            "still unavailable: " + ", ".join(unreadable)
        )
    return tuple(str(path) for path in readable)


def _run_guided_learning(
    selected,
    result,
    engine,
    *,
    seconds: float,
    teacher: bool,
    require_complete_access: bool,
) -> None:
    print(
        "\nGuided learner is read-only. Native/onboard control is preserved. It will watch "
        "every readable correlated evdev/hidraw interface while you press the physical "
        "DPI button exactly once per sample."
    )
    session = ReadOnlyLearningSession(result.device, engine.descriptors)
    if require_complete_access:
        readable = _full_access_preflight(session)
        print(
            f"Full-access preflight: {len(readable)}/{len(result.device.hidraw_nodes)} "
            "correlated hidraw sibling(s) readable."
        )

    reader = (lambda: read_teacher_labels(selected)) if teacher else None
    samples = []
    for index in range(3):
        # Capture teacher ground truth before the prompt, not immediately before
        # raw acquisition. This keeps native-teacher protocol traffic outside
        # the blind learner's capture window.
        teacher_before = dict(reader()) if reader is not None else {}
        input(
            f"[{index + 1}/3] Press Enter, then press the DPI button exactly once "
            f"within {seconds:g}s... "
        )
        sample = session.observe_action(
            seconds=seconds,
            teacher_reader=reader,
            teacher_before_state=teacher_before,
        )
        if require_complete_access and sample.unreadable_hidraw_paths:
            raise PermissionError(
                "a hidraw sibling became unavailable during full-access capture: "
                + ", ".join(sample.unreadable_hidraw_paths)
            )
        samples.append(sample)
        teacher_suffix = ""
        if sample.teacher_before_state or sample.teacher_state:
            teacher_suffix = (
                f"; teacher={dict(sample.teacher_before_state)} -> "
                f"{dict(sample.teacher_state)}"
            )
        print(
            f"  captured {len(sample.action.hid_reports)} HID report(s), "
            f"{len(sample.action.evdev_events)} evdev event(s), "
            f"{len(sample.action.feature_changes)} Feature byte change(s)"
            + teacher_suffix
        )
        if sample.unreadable_hidraw_paths:
            print(
                "  skipped unreadable hidraw sibling(s): "
                + ", ".join(sample.unreadable_hidraw_paths)
            )
    learned = session.analyze(
        samples,
        trigger_behavior=SemanticBehavior.DPI_CYCLE_TRIGGER,
    )
    print(_render_guided_learning(learned))


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.learn_window <= 0:
        parser.error("--learn-window must be greater than zero")
    if args.json and args.learn_dpi_button:
        parser.error("--json cannot be combined with interactive --learn-dpi-button")
    if args.teacher and not args.learn_dpi_button:
        parser.error("--teacher requires --learn-dpi-button")
    if args.full_access and os.geteuid() != 0:
        print(
            "Full-access discovery requires root so every correlated mouse hidraw "
            "interface can be inspected. Re-run this same discovery command with sudo. "
            "Root visibility does not authorize unknown HID writes.",
            file=sys.stderr,
        )
        return 77

    selected, status = _pick_mouse(args.device)
    if selected is None:
        return status

    if not args.json:
        mode = (
            "Known protocol detectors are disabled; unknown HID remains read-only and native control is preserved."
            if args.generic_only
            else "Unknown HID is read-only and the normal mouse-control runtime is not being reconfigured."
        )
        if args.full_access:
            mode += " Root/full-evidence acquisition is required for all correlated HID siblings."
        print(f"Discovery test mode: {mode}\n")

    engine = DiscoveryEngine(
        detectors=() if args.generic_only else None,
        save_profiles=not args.no_save,
    )
    try:
        result = engine.discover(selected)
        if args.full_access:
            access_session = ReadOnlyLearningSession(result.device, engine.descriptors)
            readable = _full_access_preflight(access_session)
            if not args.json:
                print(
                    f"Full-access acquisition: {len(readable)}/{len(result.device.hidraw_nodes)} "
                    "correlated hidraw sibling(s) readable.\n"
                )
    except TopologyError as exc:
        print(f"Discovery could not correlate the physical mouse: {exc}", file=sys.stderr)
        return 1
    except PermissionError as exc:
        print(f"Discovery permission denied: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Discovery hardware error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nDiscovery cancelled.", file=sys.stderr)
        return 130

    if args.json:
        print(json.dumps(result_to_dict(result, profile_path=engine.profile_path), indent=2, sort_keys=True))
    else:
        print(render_discovery_result(
            result,
            profile_path=engine.profile_path,
            verbose=args.verbose,
        ))

    if args.learn_dpi_button:
        try:
            _run_guided_learning(
                selected,
                result,
                engine,
                seconds=args.learn_window,
                teacher=args.teacher,
                require_complete_access=args.full_access,
            )
        except KeyboardInterrupt:
            print("\nGuided learning cancelled.", file=sys.stderr)
            return 130
        except (OSError, PermissionError) as exc:
            print(f"Guided learning hardware error: {exc}", file=sys.stderr)
            return 1

    if result.device.ambiguous:
        return 2
    if not result.device.hidraw_nodes:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
