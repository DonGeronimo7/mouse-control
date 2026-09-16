# Automatic Hardware Discovery

Mouse Control discovery treats a mouse as a physical graph of evdev and hidraw
interfaces rather than as one `/dev` node. Device paths are live interfaces,
not persistent identities.

## Safety model

1. Enumerate evdev, hidraw, and sysfs identity.
2. Correlate interfaces into one physical-device graph.
3. Parse HID descriptors for report structure only.
4. Try validated protocol detectors, currently Logitech HID++ 2.
5. If no known protocol claims the device, inspect unknown HID read-only.
6. Keep observations, correlations, validated behavior, and proven protocol
   semantics as distinct evidence levels.
7. Persist only path-independent profiles.

Unknown hardware never gains writable support from descriptor shape or changing
bytes alone. `ReadOnlyHidProbe` intentionally exposes no SET_REPORT or raw-write
API. A writable capability must carry PROVEN evidence from a validated protocol
implementation; runtime backends still enforce their own write policy.

## Identity rules

- `/dev/hidrawN` and `/dev/input/eventN` are never persistent identities.
- VID:PID alone is insufficient when multiple physical candidates exist.
- Ambiguous physical or protocol matches forbid writes.
- A USB port/sysfs parent is a connection location, not an instance identity.
- A true HID unique identifier may identify one physical instance.
- Model fingerprints describe the stable interface/descriptor layout.

## First acceptance target: Logitech G305

The G305 is the golden reference because its existing native HID++ backend is
already physically validated. Acceptance expects HID++ 4.2, dynamic ROOT lookup,
ADJUSTABLE_DPI `0x2201`, REPORT_RATE `0x8060`, ONBOARD_PROFILES `0x8100`, DPI
200–12000 in steps of 50, and report rates 125/250/500/1000 Hz. The observed
feature IDs are test facts, not hard-coded feature indexes.

## First hardware-test boundary

The discovery core is exposed through `mouse-control-discover` while the normal
`mouse-control setup` and `mouse-control run` paths remain unchanged. Runtime
profile-driven backend selection is intentionally deferred until the physical
G305 acceptance run validates topology grouping and single-responder behavior.
