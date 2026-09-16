# Changelog

## 0.8.2 — 2026-09-15

- Preserve valid remaps, configurable `dpi-cycle`, untouched DPI/polling
  settings, and unrelated configuration through setup.
- Restrict setup hardware writes to explicit relevant edits; recover from a
  temporary Generic HID/evdev fallback by promoting back to the preferred
  backend with DPI monitoring, notifications, and state reconciliation.
- Known temporary UX issue: the built-in updater can wait for invisible input.
  Type `y` and Enter to continue; this does not apply to `update --yes`.
  Planned for correction in the next development cycle.

## 0.8.1 — 2026-09-14

- Make setup revisitable, with numeric live DPI testing and Back/Cancel rollback.
- Show readable polling rates and offer selection only when safe writes are supported.
- Save accepted DPI stages so runtime cycling uses the reviewed values.

## 0.8.0 — 2026-09-14

- Add held keyboard chord bindings, setup capture, shared-modifier handling,
  and synthetic-key cleanup on disconnect and shutdown.
- Add `mouse-control run --config PATH` for isolated runtime testing.

## 0.7.11 — 2026-09-14

### RPM updater reliability

- Uses the stable RPM package name and verifies the installed RPM version after
  DNF finishes, so warnings or a nonzero DNF result do not cause a false failure
  when the target release is installed.
- Preserves the GitHub RPM fallback when a native upgrade leaves the old version
  installed, and verifies the final installed version.
- Makes `mouse-control update --yes` pass DNF `--assumeyes` for native and
  fallback installs while normal interactive updates remain unchanged.

## 0.7.10 — 2026-09-14

### Battery tray and terminal polish

- Keeps the battery tray visible through transient HID++ battery read timeouts;
  it hides only after three consecutive failed reads, and a successful read
  resets the failure count.
- Removes the redundant mouse/device name from the tray menu.
- Refreshes the terminal branding and logo.

## 0.7.9 — 2026-09-14

### AppImage portability

- Bundles a portable CPython 3.12 runtime inside the AppImage.
- Stops depending on the host Python ABI for native modules such as `evdev`.
- Fixes `_input` import failures on systems using newer Python versions.
- Makes the AppImage build dependency on a native compiler explicit in CI.

### Updater verification

- Verifies the installed RPM/DEB version after native package-manager updates.
- Falls back to the validated GitHub release package when APT/DNF reports
  success without actually upgrading Mouse Control.

## 0.7.8 — 2026-09-14

### Updater verification

- The RPM/DEB updater now verifies the installed package version after DNF/APT
  runs instead of treating a successful process exit as proof of an upgrade.
- When a native repository reports success but leaves Mouse Control unchanged,
  such as a direct-GitHub RPM/DEB install with “nothing to do,” the updater
  falls back to the validated GitHub release package.
- GitHub package installs are verified too; success is reported only after the
  installed version reaches the requested release.

## 0.7.7 — 2026-09-14

### Launcher and icon polish

- Replaces the generic ASCII launcher mouse with a terminal-native rendition
  of the Mouse Control segmented mouse mark.
- Strengthens the neon application icon contours and central details for
  clearer recognition in desktop launchers, menus, taskbars, and small sizes.

## 0.7.6 — 2026-09-14

### Release packaging

- Makes the universal updater, terminal-native launcher, and icon assets the
  user-facing release after the unpublished v0.7.5 tag.
- Adds the missing Arch `python-setuptools` build dependency.

## 0.7.5 — 2026-09-13

### Universal updater

- Adds `mouse-control update` and `mouse-control update --check` for the
  documented RPM/DNF, DEB/APT, AppImage, and Python/pip installation methods.
- Delegates updates to the installation owner: system-package files are never
  overwritten manually, AppImage replacement is atomic, and source/editable or
  unsupported installations are reported without modification.
- Validates release asset names and architecture before selection, preserves
  user configuration and service state, and keeps `--check` non-mutating.

## 0.7.4 — 2026-09-13

### Community hardware support reports

- Adds on-demand `mouse-control support` reports for investigating unsupported
  mice. Reports are local, read-only, privacy-safe, and include optional guided
  button capture plus deterministic HID descriptor topology.
- There is no telemetry, automatic upload, background monitor, or hardware
  write during support probing. Unknown hardware remains reportable.

### Added

- Optional battery monitoring tray integration with a programmatically drawn
  StatusNotifierItem battery icon and proportional live fill.
- Live StatusNotifierItem tooltip and standard `com.canonical.dbusmenu`
  right-click menu with device name, exact battery percentage, and status when
  available.
- Synchronized icon, tooltip, and menu updates from one `BatteryState`.

### Fixed

- `dbus-next` `IconPixmap` marshalling: struct bodies are Python lists and
  pixel payloads are immutable bytes.
- Battery menu refreshes now emit the standard property and layout updates.

### Included stabilized v0.7.x work

- Native Logitech HID++, reconnect and late receiver-insertion recovery,
  physical DPI event monitoring, and service CLI controls.
- Removal of the active Libratbag dependency for Logitech control; OpenRazer
  remains optional.

## 0.7.2 — 2026-09-13

### Added

- First-class native Logitech HID++ 2 backend with dynamic ROOT feature
  discovery, DPI enumeration/read/write/readback, physical DPI events, and
  report-rate enumeration/read.
- `mouse-control start`, `stop`, `restart`, and `status` service commands;
  `mouse-control run` remains foreground/debug execution.
- Reconnect-safe native HID backend rediscovery, late receiver insertion, and
  watcher/monitor recovery.

### Changed

- Logitech DPI and report-rate control no longer depends on Libratbag,
  ratbagd, or ratbagctl. OpenRazer remains optional for supported Razer mice.
- Backend discovery now keeps generic HID diagnostics and evdev remapping as
  the safe fallback for unknown hardware.
- DPI monitoring uses confirmed live HID++ notifications without forcing a
  profile-mode switch.

### Fixed

- Stale backends after late receiver insertion and evdev/HID reconnects.
- Suppression of the first confirmed DPI event, report-rate write capability
  handling, and notification monitor recovery after HID reconnect.

### Packaging and hardware validation

- Refreshes Python, RPM, Debian, AppImage, and Arch/AUR release metadata.
- Physically validated on the Logitech G305 Lightspeed Wireless Gaming Mouse:
  native detection; 200–12000 DPI in 50-DPI steps; configured 800/1500/2000/
  2500/3000 stages; physical DPI events and OSD; 1000/500/250/125 Hz discovery;
  reconnect and late-insertion recovery; and side-button remapping.

## 0.6.9 — 2026-09-12

### Community hardware-testing preparation

- Adds read-only `mouse-control doctor` diagnostics, a privacy-safe
  `doctor --report` format, and conservative `doctor --fix` package guidance.
- Adds Debian packaging metadata, a clean Arch/AUR `PKGBUILD`, and AppImage
  build preparation. Native packages remain the recommended path for host udev,
  systemd, input permissions, and optional hardware daemons.
- Adds a hardware compatibility matrix and GitHub hardware/bug issue templates
  for community reports. Generic evdev remapping remains the fallback when
  optional Libratbag, OpenRazer, or HID++ capabilities are unavailable.
- Carries forward the physically validated Logitech G305 HID++ behavior
  unchanged: dynamic Adjustable DPI discovery and independent, cached passive
  Onboard Profiles DPI-event metadata; normal runtime remains passive/read-only.

## 0.6.3 — 2026-09-11

### Controlled HID++ discovery and passive runtime

- Adds controlled Logitech HID++ capability discovery using dynamic ROOT feature
  lookup, including the HID++ Device Name (`0x0005`) and Adjustable DPI
  (`0x2201`) features. Explicit discovery temporarily coordinates with ratbagd,
  stores selected schema-versioned metadata, and restores ratbagd afterward.
- Keeps normal runtime read-only and passive: it loads cached metadata, performs
  no active ROOT discovery, and leaves ordinary Libratbag configuration and
  input remapping available when notification metadata is absent or invalid.
- Keeps the G305 Adjustable DPI query route (feature index `0x1a`) independent
  from the learned Onboard Profiles notification route (feature index `0x07`).
  Conflicting passive indexes fail safely and unknown devices never guess one.
- Restores reliable native DPI-cycle notifications for the physically validated
  Logitech G305 (`046d:4074`), including the sequence 800, 1500, 2000, 2500,
  3000, then 800 DPI. The firmware button remains `resolution-cycle-up`.
- Preserves the existing Ratbag/OpenRazer/Generic backend architecture, optional
  OpenRazer integration, setup/service hardening, keyboard capture, remapping,
  polling, and clean shutdown behavior. No Solaar dependency is introduced.

Only the Logitech G305 `046d:4074` is physically validated for HID++ DPI
notifications in this release. Other compatible Logitech devices may be
discoverable, but their notification behavior is not claimed as validated.

## 0.5.0 — 2026-09-11

### Validated Logitech G305 DPI notifications

- Adds passive, read-only HID++ DPI-stage monitoring for the physically validated
  Logitech G305 (`046d:4074`). The native firmware DPI button remains
  `resolution-cycle-up`; setup does not rewrite mouse firmware or use a BTN_TASK
  workaround.
- Resolves the hidraw node dynamically, reconnects when the node changes, scopes
  HID++ feature index `0x07` to the G305 profile, and maps stage indices through
  configured DPI stages. Duplicate consecutive events are suppressed.
- Sends a fresh Freedesktop notification for each real DPI transition. Notification
  or HID++ failures remain nonfatal to normal remapping.

### Reliability, permissions, and packaging

- Preserves setup cancellation/failure configuration safety and restoration of a
  previously running user service; shutdown handles SIGINT/SIGTERM cleanly.
- Installs active-session uaccess rules for mouse input, uinput, and the correctly
  parent-matched G305 HID++ interface (`KERNELS=="0003:046D:4074.*"` and
  `DRIVERS=="logitech-hidpp-device"`).
- Publishes the complete maintained source tree, including notifications, HID++
  monitor, hardware backends, udev data, and 89 tests. OpenRazer remains optional.

## 0.4.2 — 2026-09-10

Includes both changes developed since the published 0.3.3 package.

### Physical keyboard-key capture

- Wizard option 3 captures a physical keyboard or media-key press and saves the
  existing `key:KEY_*` mapping format, including Super, function and volume keys.
- Listens across accessible keyboard interfaces, deduplicates paths, and filters
  queued events, held keys and autorepeat. Ctrl+C cancels capture.
- Preserves manual key-code entry as option 5 and restores terminal state.

### Hardware backends and OpenRazer

- Adds a capability-based interface and ordered Ratbag, OpenRazer and Generic
  backend selection. Future backends can be registered without vendor branches
  in the wizard or remapper.
- Migrates Libratbag code while preserving G305 USB identity matching, enabled DPI
  stages, active/default resolution selection, maximum polling setup and runtime
  active-DPI fallback. Existing TOML configuration remains compatible.
- Adds optional OpenRazer Python/daemon support for device-reported DPI and polling
  capabilities. Matches USB VID/PID, rejects ambiguous matches and avoids guessed
  polling-rate limits. OpenRazer applies active DPI, not programmable stages.
- Hardware discovery/query/write failures warn and allow generic remapping to
  continue. No mandatory OpenRazer dependency, lighting, profiles or macros.
- Documents optional Fedora OpenRazer installation and real-hardware limitations.

### Packaging and validation

- Synchronizes RPM, Python project metadata and `__version__` to 0.4.2.
- Includes the existing evdev tuple-alias fix directly in source; the old packaging
  patch is no longer applied.
- Retains the existing remapper and systemd service behavior. RPM installation
  does not start the remapper, enable services or change input permissions.
- Full suite: 38 tests passed; Python compile checks passed. Physical Razer/G305
  smoke tests and system-wide RPM installation have not been performed.

## 0.3.3 — 2026-09-10

- Published Fedora 44 / Python 3.14 RPMs from the existing utility with its setup
  wizard and systemd user-service commands.
- Updated package/Python version metadata and included the evdev tuple button-name
  compatibility fix. Six tests passed during that package build.

## 0.2.5 — 2026-09-10

- Initial local RPM packaging of the existing mouse-control utility.
- Included service controls and the evdev tuple-alias compatibility patch.
