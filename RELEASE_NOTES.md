# Mouse Control v0.8.2

This stabilization release keeps ordinary evdev/uinput remapping independent
of optional hardware backends while native hardware control recovers cleanly.

- Setup preserves valid remaps, including the configurable `dpi-cycle` action,
  and retains untouched DPI/polling settings.
- Setup writes hardware settings only for explicit relevant edits and merges
  configuration without discarding unrelated or unknown entries.
- On reconnect, Mouse Control can temporarily use Generic HID/evdev, then
  promote back to the preferred native or vendor backend. Native promotion
  restores DPI monitoring, notifications, and DPI/polling reconciliation.

**Updater note:** When updating through Mouse Control's built-in updater,
installation may pause without displaying the package manager's confirmation
prompt. If this happens, type `y` and press Enter. The update will then
continue and finish normally. This does not apply to `mouse-control update
--yes`. This temporary known UX issue—waiting for invisible user input—is
planned for correction in the next development cycle.

---

# Mouse Control v0.8.1

The setup wizard now lets you revisit DPI, polling, and button choices from
Review without losing accepted edits. Enter a DPI value to test it immediately;
Back discards that test, and cancelling setup restores the original DPI when
possible.

The polling screen shows hardware-reported rates and the current rate when
readable. It offers selection only when the backend permits safe writes.
Finishing saves the exact accepted DPI stages for normal runtime cycling.

---

# Mouse Control v0.8.0

Mouse buttons can now hold keyboard chords, for example
`chord:KEY_LEFTCTRL+KEY_LEFTSHIFT+KEY_S`. The keys stay held while the mouse
button is pressed. Overlapping chords safely share modifiers, and disconnect
or shutdown releases synthetic keys so they do not remain stuck.

The setup wizard can capture a chord, and you can also enter one manually in
the configuration. Chord support is not general macro/sequencing support.

For isolated configuration and runtime testing, use
`mouse-control run --config /path/to/config.toml`. This runs with the selected
file without changing the normal installed configuration.

---

# Mouse Control v0.7.11

## RPM updater reliability

RPM ownership detection now retains the stable package name. After DNF runs,
Mouse Control verifies the installed RPM version; warnings, noisy output, or a
nonzero DNF result cannot report a false failure when the target version is
installed.

If a native DNF upgrade leaves the old version installed, Mouse Control still
uses the validated GitHub RPM fallback and verifies the final installed version.

`mouse-control update --yes` now passes DNF `--assumeyes` for both native and
fallback installs. Updates without `--yes` keep their normal confirmation flow.
