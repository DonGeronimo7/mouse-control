# Mouse Control Project Status

Last updated: 2026-09-15

## Current architecture

Mouse Control keeps evdev/uinput remapping independent from hardware control.
Hardware discovery preserves all matching HID interfaces for a physical input
device. `NativeHidBackend` lets protocol drivers probe those interfaces and
refuses hardware writes if more than one interface claims the protocol.

One `HardwareSupervisor` owns the selected physical backend and exposes a
stable reference to DPI cycling, DPI events, polling control, and battery
status. Startup and reconnect use the same explicit `DesiredHardwareState`
reconciliation. Backend replacement is generation-guarded, closes the old
backend, resolves the current exact transport plus VID:PID without guessing
ambiguous devices, and reapplies safe live polling and DPI state before event
monitoring resumes.

One `HidSession` owns each selected hidraw interface. It serializes writes,
correlates command replies, dispatches unsolicited events without competing
readers, logs subscriber failures without killing the reader, and wakes
pending requests on disconnect.

The first driver is Logitech HID++ 2. It discovers feature-table indexes live
through ROOT and implements Adjustable DPI (`0x2201`) enumeration, reads,
validated writes, and write verification. Onboard Profiles (`0x8100`) events
are recognized by their dynamically discovered index; a resolution-slot event
causes a current-DPI query and emits canonical confirmed state.

Report Rate (`0x8060`) list/read/write and readback verification are implemented.
Onboard Profiles (`0x8100`) live mode read/write mechanics are separate from
backend safety policy. Automatic Onboard -> Host transition is allowed only
for the USB Logitech G305 (`0003:046d:4074`), is verified before the rate
write, rolls back to the original mode if the transaction fails, and remains
in Host mode after a verified success. Unknown mice already in Host mode may
use their advertised live report-rate control, but are never transitioned
automatically. Persistent profile programming is deliberately not claimed. Its complete
record format needs additional protocol fixtures and physical write validation
so unrelated profile fields are never damaged.

Native HID exposes protocol-neutral read-only battery state when a validated
HID++ battery feature is available. ROOT discovery selects Unified Battery
(`0x1004`) or Battery Status (`0x1000`) with separate decoders. The physically
observed G305 exposes `0x1000` at a dynamically resolved index of `0x05`; its
observed `5a 32 00` payload decodes to 90% discharging. The optional SNI tray
item draws a compact monochrome battery with proportional ARGB fill (without
percentage text), and supplies the exact percentage and status through its
tooltip and standard DBusMenu. Icon, tooltip, and menu derive from the same
BatteryState and refresh together.
Extended Adjustable DPI (`0x2202`) is discoverable but independent-axis packet
handling is likewise deferred.

OpenRazer remains available behind the common backend contract. Unknown or
ambiguous hardware falls back to Generic HID diagnostics and ordinary evdev
remapping without guessed capabilities or writes.

Backends now return confirmed DPI state where live readback is available.
Legacy setters without readback remain compatible, while `DpiCycler` records
their result as requested/unconfirmed rather than treating it as hardware fact.

Freedesktop DPI notifications consume canonical DPI values. Each update uses
`replaces_id = 0` for every confirmed physical transition, and notification failures do
not stop hardware handling or remapping.

## Validation status

The native session, HID++ transaction, lifecycle, reconnect, notification,
remapping, configuration, service, and backend behavior use deterministic fixtures and require no
physical hardware in CI. The Logitech G305 Lightspeed Wireless Gaming Mouse is
the reference identity for automatic native detection and Host-mode policy.
The new transactional polling writes, Host-mode physical DPI-event behavior,
and unified state reconciliation are covered by fixtures but still require the
physical acceptance procedure in `docs/G305_HARDWARE_ACCEPTANCE.md`. Other
Logitech devices remain subject to device-specific physical validation.

Future hardware support should supply a capability adapter for an exactly
identified and validated protocol, then pass the universal behavior suite.
Hardware additions should not redesign established remapping, notification,
lifecycle, or service behavior.

## 2026-09-15 acceptance blocker investigation

Automated validation: 295 tests pass, including 11 new cases spanning actual
HID++ ROOT discovery, native policy, optional supervisor forwarding, setup
choices, and verified/failed transactions. Compile and whitespace checks pass.
Python sdist/wheel and local Fedora RPM builds pass; RPM `%check` also passes
295 tests, compileall, and the staged CLI smoke test. A wheel installed in an
isolated virtual environment passes `mouse-control --help`.
No polling implementation change was required: reviewed commit `1c7b23f`
already contains the correction to the old capability policy.

Physical validation: the user reported a passing initial doctor/environment
checkpoint and a blocked first polling screen. No polling write occurred in
that checkpoint; no physical writes were performed in this investigation.
The system-installed 0.8.1 still contains the old discovery-time Host-only
writability flag and lacks the new transaction, while the checkout uses the
G305-specific policy. An isolated fixture against that installed package
reproduced readable `1000/500/250/125`, current `1000`, writable `False`.

Remaining physical acceptance: execute the explicit source command in
`G305_HARDWARE_ACCEPTANCE.md` for `1000 -> 500 Hz`; install matching code for
the service before continuing lifecycle acceptance. Package version alone is
not proof of source parity. All rate transitions, combined Host-mode behavior,
and reconnect acceptance remain pending.

## 2026-09-15 setup remap preservation

Setup now begins with the existing valid `[remap]` table and changes only
buttons explicitly captured in the wizard. Skip/keep mappings is idempotent,
including the canonical `dpi-cycle` action. The button-action menu offers
`dpi-cycle` directly; no device or button code receives an implicit DPI role.
The G305 physical validation sequence remains pending: confirm the
`BTN_FORWARD = 'dpi-cycle'` mapping survives setup, restart Mouse Control, and
verify physical DPI cycling before attempting the verified `500 -> 250 Hz`
polling transition.

## 2026-09-15 setup configuration preservation

Setup now loads persisted desired DPI stages, active DPI, polling rate, remaps,
and notifications before probing hardware. Capability discovery is display-only:
it reports supported/current hardware state without replacing valid preferences.
Only an explicit DPI or polling selection results in the corresponding hardware
write. Setup merges its owned fields into the parsed TOML and retains unknown
forward-compatible tables. The prior physical polling persistence observation
is therefore **inconclusive**: setup's destructive initialization, not Native
HID/HID++ behavior, could have restored 1000 Hz.

Physical acceptance remains required after installing this build: verify
custom DPI/active DPI, `BTN_FORWARD = 'dpi-cycle'`, other mappings, and a
configured 500 Hz rate survive setup; then run the documented 1000→500→250→125→1000
polling sequence and separate reconnect acceptance.

## 2026-09-15 reconnect promotion after partial enumeration

Physical reproduction: a normally operating G305 using Native HID/HID++ was
unplugged and reinserted. During incomplete receiver enumeration the
supervisor rebound to Generic HID / evdev. Evdev remapping recovered, but no
later Native HID rebind occurred, so Native-HID DPI event monitoring and DPI
notifications remained unavailable until a daemon restart.

Root cause: after Native HID had cleared `HardwareSupervisor.discovery_pending`,
a Generic fallback rebind did not set it again. `DpiMonitorSupervisor` then
treated Generic's unsupported DPI events as conclusive and used its unsupported
wait path instead of continuing supervisor rebind discovery. This was a
backend-selection lifecycle defect, not a notification delivery failure.

Correction: a Generic fallback following any previously selected non-Generic
backend is provisional. The supervisor continues retrying discovery, retains
the current Generic backend without generation churn for repeated Generic-only
probes, and atomically promotes to Native when it becomes safely available.
Promotion closes the superseded fallback, advances the generation, and lets
existing generation-aware consumers bind a fresh DPI event watcher.

Automated validation: 305 tests pass, including delayed Native HID discovery
after a Generic fallback and DPI event/notification recovery after promotion.
`python3 -m compileall -q src tests` and `git diff --check` pass. Physical
receiver-reinsert validation on the G305 remains pending; no physical recovery
claim is made from the fixtures alone.
