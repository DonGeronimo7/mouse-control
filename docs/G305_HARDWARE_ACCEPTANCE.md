# Logitech G305 core hardware acceptance

Run this checklist on the USB Logitech G305 Lightspeed receiver
(`0003:046d:4074`) after installing the stabilization branch. These checks are
intentionally physical and are not claimed by the automated suite.

## Preparation

1. Back up `~/.config/mouse-control/config.toml`.
2. Run `mouse-control doctor` and confirm the selected mouse is the G305.
3. Run `mouse-control setup`, retain stages `800, 1500, 2000, 2500, 3000`,
   select a polling rate, enable DPI notifications, and retain at least one
   mouse-to-key or keyboard-chord mapping.
4. Start the service with `mouse-control restart`.
5. Follow service logs in a second terminal with
   `journalctl --user -u mouse-control.service -f`.

Do not use tools that write onboard profile sectors. Mouse Control should make
only live mode, report-rate, and adjustable-DPI changes.

## Polling transaction

Use `mouse-control setup` to apply each transition in order:

1. `1000 -> 500 Hz`
2. `500 -> 250 Hz`
3. `250 -> 125 Hz`
4. `125 -> 1000 Hz`

For every transition, confirm that setup reports a verified write, subsequent
hardware readback shows the requested value, pointer input remains responsive,
and no onboard profile, button binding, DPI table, lighting state, or other
persistent setting changes unexpectedly. Confirm the device remains in Host
mode after each successful transaction.

## DPI button and notifications

1. Begin at `800 DPI`.
2. Press the physical DPI button slowly and verify
   `1500 -> 2000 -> 2500 -> 3000 -> 800`.
3. For each press, confirm cursor sensitivity changes, hardware readback equals
   the popup value, and exactly one distinct popup appears.
4. Press rapidly through at least two full cycles. Confirm all ten requested
   transitions occur in order and all ten popups are visible/queued; none are
   coalesced.
5. Confirm a firmware/off-list `800` value never displaces the configured
   sequence. If starting from any off-list value, the next press must select
   the first configured stage.
6. Disable DPI notifications and confirm physical and software DPI cycling
   continue without popups. Re-enable notifications afterward.

## Combined Host-mode behavior

While the G305 is in Host mode, verify all of the following together:

- physical DPI-button cycling;
- mapped `dpi-cycle` action;
- normal pointer/button passthrough;
- disabled and mouse-to-mouse mappings;
- mouse-to-key mappings and keyboard chords, including correct release;
- battery/status tray updates;
- one notification per DPI transition.

## Reconnect and lifecycle

With the service running:

1. Turn the mouse off, wait several seconds, and turn it on.
2. Confirm remapping recovers without restarting the service.
3. Confirm configured polling and active DPI are restored by hardware readback.
4. Confirm physical and software DPI cycling, notifications, and battery status
   resume without duplicate monitors or duplicate popups.
5. Unplug the receiver, wait several seconds, and reinsert it.
6. Repeat the same remapping, polling, DPI, notification, and battery checks.
7. During a reconnect retry, run `mouse-control stop` and confirm shutdown is
   prompt and leaves no stuck virtual key/button output.

## Service lifecycle

Run each command and verify its reported state and behavior:

```text
mouse-control stop
mouse-control start
mouse-control restart
mouse-control status
```

After `restart`, confirm configured polling, active DPI, physical/software DPI
cycling, remapping, keyboard mappings, notifications, and battery reporting all
return. Review the journal for rollback failures, ambiguity refusals, reader
errors, or repeated reconnect loops before accepting the branch.

## Resume after the 2026-09-15 polling-choice blocker

The initial doctor checkpoint passed, but the first `1000 -> 500 Hz` step
stopped at "Report-rate changes are unavailable", before any polling write.
Investigation found `/usr/bin/mouse-control` loading the older system package
from `/usr/lib/python3.14/site-packages/mouse_control`, not this checkout.
Both report version `0.8.1`; a branch/HEAD check and version number alone do
not establish which implementation setup is executing.

The installed driver's discovery cached `writable = (mode == Host)`. Its
backend forwarded that boolean and had no Onboard -> Host transaction.
The reviewed checkout already separates protocol capability from the exact
USB G305 transition policy. Deterministic fixtures reproduce the installed
failure and exercise the checkout's selectable and verified transaction.
The actual physical control mode was not captured by this investigation;
Onboard mode reproduces the observed output, but is not a new physical reading.

To resume setup against the tested checkout, first run this exact command:

```sh
PYTHONPATH=/home/mgeronimo/Mouse-control/src python3 -m mouse_control.cli setup
```

Retain the existing mappings and stages `800, 1500, 2000, 2500, 3000`.
At Polling rate, confirm current hardware readback is `1000 Hz`, choose
`2. 500 Hz`, and accept the reviewed configuration. Require the message
`Hardware polling rate set and verified at 500 Hz through Native HID.`
If choices remain unavailable or verification fails, stop and preserve the
error; do not advance to the next rate. No physical rate transition has been
validated by this engineering session.

Before using ordinary `mouse-control setup` or continuing service/reconnect
acceptance, install the locally built stabilization package and restart the
service so both CLI and daemon use it. The source command above selects code
for that invocation only; it does not upgrade the installed service. A local
RPM with the same NEVRA as the installed `0.8.1-1` needs a reinstall operation,
not an ordinary upgrade. Verify the loaded module path and compare the installed
`hidpp_driver.py` and `hardware/native_hid.py` against this checkout; a shared
version string is insufficient. Continue `500 -> 250 -> 125 -> 1000` only after
the first physical transition succeeds, then complete all sections above.
