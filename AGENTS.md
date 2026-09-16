# Mouse Control Agent Instructions

## Project intent

Mouse Control is a headless Linux mouse remapper. Keep evdev/uinput remapping
independent from optional hardware configuration. A missing, unsupported, or
failed hardware backend must never prevent ordinary remapping from starting.

## Hardware safety rules

- Match devices by exact transport plus USB/Bluetooth VID:PID. Do not use a
  product name as authority for hardware writes.
- Never guess HID feature indexes, report IDs, payload formats, DPI values, or
  polling rates. Empty capability lists mean unknown, not a default range.
- Keep generic HID inspection read-only. Enable output/feature reports only in
  a narrowly scoped protocol driver backed by device-specific evidence and
  tests.
- Refuse ambiguous hidraw or backend matches rather than choosing one.
- Translate hardware failures to `HardwareError`; preserve evdev remapping.
- Keep Logitech HID++ feature indexes dynamic. Resolve feature IDs through the
  HID++ ROOT feature table.
- Treat the Logitech G305 (`046d:4074`) as the currently validated reference
  device. Do not generalize its event payloads or receiver slot to other mice.
- Do not broaden hidraw udev access. Add exact, reviewed device/interface rules
  after the relevant VID:PID and driver path are known.

## Automatic hardware discovery invariants

- Discovery learns hardware; it does not guess hardware.
- Unknown hardware is observed before it is modified. Generic discovery must
  never issue SET_REPORT, HIDIOCSFEATURE, HIDIOCSOUTPUT, HIDIOCSINPUT, or raw
  hidraw writes.
- `/dev/hidrawN` and `/dev/input/eventN` are live interfaces, never persistent
  hardware identities.
- A USB/sysfs parent path is a connection location, not an instance identity.
  Only a true device-unique identifier may identify one physical instance.
- VID:PID alone may not authorize writes when multiple physical candidates
  exist. Ambiguous physical or protocol matches must refuse writes.
- Descriptor shape and changing bytes are evidence, not vendor semantics.
  Writable capability claims require `EvidenceLevel.PROVEN` evidence from a
  validated protocol implementation.
- Known-protocol feature indexes remain dynamic when the protocol exposes a
  discovery mechanism; HID++ feature IDs are resolved through ROOT.
- Capability failures remain independent. Failure to discover DPI must not
  erase report-rate, battery, button, or other independently proven support.
- Discovery profiles must be path-independent and may cache only proven facts;
  runtime backends still enforce their own write policy.
- Do not make automatic discovery part of `mouse-control run` or setup until
  the G305 physical acceptance test validates topology grouping and exactly one
  protocol responder.

## Current backend policy

- Use the native HID session and protocol drivers for validated hardware
  control. Do not reintroduce daemon ownership of HID protocol traffic.
- Keep OpenRazer optional.
- The final fallback is Generic HID identity/diagnostics plus evdev remapping;
  it must not claim standardized DPI or polling controls because USB HID does
  not define them.
- Prefer a validated native protocol backend only after its read/write behavior
  is covered by tests and physical validation.

## Development workflow

- Read `docs/AI_HANDOFF_PROTOCOL.md` before beginning implementation work.
- Read `docs/PROJECT_STATUS.md` before changing hardware architecture.
- Read the latest entry in `docs/AI_HANDOFF_LOG.md` when it exists.
- Add tests for identity matching, ambiguity, malformed reports, unsupported
  capabilities, and hardware failure fallback.
- Run `python3 -m pytest -q`, `python3 -m compileall -q src tests`, and
  `git diff --check` before handing off a change.
- Do not commit generated build artifacts or local hardware captures containing
  serial numbers or unrelated USB device information.
- Do not merge, tag, publish packages, create a release, or push to `main`
  unless the user explicitly requests that exact action.

## Task handoff format

Follow `docs/AI_HANDOFF_PROTOCOL.md`. End implementation tasks with: goal,
branch/commit and working-tree state, files changed, behavior changed,
automated and physical validation with an explicit validation level, evidence,
remaining risks/blockers, and the recommended next bounded task.
