# Mouse Control

![Mouse Control — Linux gaming mouse configuration and remapping](assets/mouse-control-social-preview.png)

Open-source Linux gaming mouse configuration and remapping: map mouse buttons to keyboard keys, configure supported DPI and polling rates, and help test new hardware.

[![Current release](https://img.shields.io/github/v/release/DonGeronimo7/mouse-control?display_name=tag&label=release)](https://github.com/DonGeronimo7/mouse-control/releases/latest)
[![CI](https://github.com/DonGeronimo7/mouse-control/actions/workflows/ci.yml/badge.svg)](https://github.com/DonGeronimo7/mouse-control/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-3DA639)](LICENSE)
[![Platform: Linux](https://img.shields.io/badge/platform-Linux-FCC624?logo=linux&logoColor=black)](https://github.com/DonGeronimo7/mouse-control)

Mouse Control is a Linux gaming mouse utility for people whose vendor software
does not work on Linux. It remaps extra mouse buttons through evdev/uinput,
including mapping a mouse button to a keyboard key, disabling a button, or
passing it through. This input-level approach is suited to modern Linux desktop
sessions, including Wayland, without relying on X11-specific remapping.

Where a device has a validated hardware backend, Mouse Control can also expose
DPI and polling/report-rate configuration. Native Logitech HID/HID++ support
discovers capabilities from the device; OpenRazer is optional for applicable
Razer hardware. When those controls are unavailable, ordinary mouse remapping
continues to work. Generic USB HID does not standardize DPI or polling-rate
writes, so Mouse Control never guesses them.

The current release is [v0.8.2](https://github.com/DonGeronimo7/mouse-control/releases/tag/v0.8.2).

## What Mouse Control does

- Remaps gaming-mouse buttons, including side buttons, to mouse actions or keyboard keys.
- Disables or passes through buttons when that fits a game or desktop workflow.
- Configures DPI and polling/report rate only when a detected backend reports that capability.
- Uses native Logitech HID++ discovery for supported capabilities; the Logitech G305 is the physically validated reference device.
- Uses optional OpenRazer integration for supported Razer hardware without making it a requirement for remapping.
- Falls back to read-only generic HID diagnostics and evdev/uinput remapping when advanced hardware control is unavailable.
- Invites community testing of unsupported mice with a privacy-safe support report.

## Get Mouse Control

Get the latest package from the [v0.8.2 release](https://github.com/DonGeronimo7/mouse-control/releases/tag/v0.8.2):

- **Fedora, Nobara, and other RPM distributions:** RPM
- **Debian, Ubuntu, Mint, and other DEB distributions:** DEB
- **Other distributions:** AppImage
- **Arch Linux:** included [PKGBUILD](PKGBUILD)
- **Developers and advanced users:** source installation

Detailed commands and package filenames are in [Install](#install).

## Updating

Run `mouse-control update` to check the latest stable GitHub release and update
the installation currently running the command. Mouse Control detects its
documented installation methods and delegates to the owning native mechanism
(DNF, APT, a user-owned AppImage, or the active Python environment). Use
`mouse-control update --check` for a non-modifying availability check.

**Updater note:** When updating through Mouse Control's built-in updater,
installation may pause without displaying the package manager's confirmation
prompt. If this happens, type `y` and press Enter. The update will then
continue and finish normally. This temporary known UX issue—waiting for
invisible user input—is planned for correction in the next development cycle.
This does not apply to `mouse-control update --yes`.

Repository packages can intentionally lag behind the newest GitHub release.
Editable/source and unsupported installation methods are reported with safe
next steps rather than changed automatically.

## Unsupported gaming mouse? Help expand compatibility

Run:

```bash
mouse-control support
```

This creates a local, privacy-safe, read-only report for the mouse you select.
Nothing is uploaded automatically and probing performs no hardware writes.
Attach the report to the [hardware compatibility issue template](https://github.com/DonGeronimo7/mouse-control/issues/new?template=hardware-compatibility.yml) to help expand Linux gaming-mouse compatibility. `mouse-control support --guided` can additionally record one requested side-button press.

Unknown hardware is expected: a useful report does not mean the device is
already supported. In particular, this project does not claim support for the
Turtle Beach Kone II.

## Install

Choose the package that fits your Linux distribution. Native packages are the
best choice when available: they install the dependencies and udev integration
needed by the host system.

### Fedora, Nobara, and other RPM-based distributions

Download the v0.8.2 RPM from the [release assets](https://github.com/DonGeronimo7/mouse-control/releases/tag/v0.8.2), then install it with DNF:

```bash
sudo dnf install ./mouse-control-0.8.2-1.fc44.noarch.rpm
```

This unsigned Fedora 44 package declares Python, evdev, dbus-next, and systemd
dependencies and native HID integration. OpenRazer is optional. If an older pip
installation shadows the command, use `/usr/bin/mouse-control`.

### Debian, Ubuntu, Mint, and other DEB-based distributions

Download `mouse-control_0.8.2-1_all.deb` from the [release assets](https://github.com/DonGeronimo7/mouse-control/releases/tag/v0.8.2), then let APT resolve its declared dependencies:

```bash
sudo apt install ./mouse-control_0.8.2-1_all.deb
```

### AppImage and other distributions

Download [Mouse-Control-0.8.2-x86_64.AppImage](https://github.com/DonGeronimo7/mouse-control/releases/download/v0.8.2/Mouse-Control-0.8.2-x86_64.AppImage), make it executable, and run it:

```bash
chmod +x Mouse-Control-0.8.2-x86_64.AppImage
./Mouse-Control-0.8.2-x86_64.AppImage setup
```

The AppImage can contain the Python application and user-space libraries such
as Python, evdev, and dbus-next as provided by its build. It does not bundle
host system components: systemd, udev rules, kernel input support, or
the OpenRazer daemon/driver remain distribution-managed. Do not install a
permanent user service from an arbitrary AppImage download location. Use the
native package when you need the packaged udev and service integration.

### Arch Linux

The repository includes a [PKGBUILD](PKGBUILD) prepared for v0.8.2; it needs
the v0.8.2 tag before it can build. This project does not currently claim to
publish an AUR package. OpenRazer remains an optional dependency.

### Source installation

For developers and advanced users, download the source distribution from the
[release assets](https://github.com/DonGeronimo7/mouse-control/releases/tag/v0.8.2), or clone this repository:

```bash
git clone https://github.com/DonGeronimo7/mouse-control.git
cd mouse-control
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Source installations may need their distribution's Python development headers
to build `evdev`. See [Device permissions](#device-permissions) for the udev
rule required by a source installation.

## Quick start

## Desktop launcher

Native package installs add **Mouse Control** to the Linux application launcher.
Selecting it opens the interactive terminal-native application in the desktop
environment's configured terminal; Mouse Control does not use a graphical
desktop toolkit. You can still invoke individual workflows directly:

```bash
mouse-control setup
mouse-control support
mouse-control status
```

Running `mouse-control` with no arguments opens the same interactive home
screen when used from a terminal. In scripts or redirected output it prints
normal command help instead of waiting for input.

Run setup as your logged-in desktop user:

```bash
mouse-control setup
```

The wizard detects or selects a mouse, records buttons, configures mappings,
and saves a configuration. For the normal background service workflow:

```bash
mouse-control start
mouse-control status
mouse-control restart
mouse-control stop
```

Run `mouse-control install-service` once before `start`. `mouse-control run`
remains the explicit foreground/debug command; it does not start the service.

## How Mouse Control fits into the Linux mouse ecosystem

Mouse Control combines mouse-focused evdev/uinput remapping, selected
hardware-control backends, and a community hardware-reporting path. It can be a
useful complement to established Linux mouse tools:

- [Piper](https://github.com/libratbag/piper) is a GTK application to configure gaming devices and works with the [libratbag](https://github.com/libratbag/libratbag) configuration daemon.
- [Solaar](https://github.com/pwr-Solaar/Solaar) is a Linux device manager for Logitech devices.
- [Input Remapper](https://github.com/sezanzeb/input-remapper) changes the behavior of a broader range of Linux input devices.
- [OpenRazer](https://github.com/openrazer/openrazer) provides a Linux driver and user-space daemon for Razer features; Mouse Control can use it optionally where applicable.

Mouse Control does not require libratbag or OpenRazer for ordinary evdev/uinput
remapping, and its native Logitech HID++ path is independent of libratbag.

## Frequently asked questions

### Does Mouse Control work on Wayland?

Mouse Control remaps through Linux evdev/uinput rather than an X11-specific
remapping layer, so its core remapping model is suitable for Wayland desktop
sessions. Device access and optional keyboard-key capture still depend on the
active session's permissions and desktop policy.

### Can I remap gaming-mouse buttons on Linux?

Yes. Mouse Control can pass through, disable, or remap mouse buttons to mouse
actions or keyboard keys. This software remapping remains available separately
from vendor-specific hardware features.

### Can I change gaming-mouse DPI on Linux?

Only when a detected, validated backend exposes writable DPI capability for the
selected device. Generic USB HID does not define a standard DPI-control command,
so Mouse Control does not make unverified DPI writes.

### Can Mouse Control change mouse polling rate on Linux?

Only when the selected device reports supported polling/report rates through a
validated backend. Mouse Control verifies reported capabilities rather than
assuming a default rate or generic HID command.

### What if my gaming mouse is unsupported?

Run `mouse-control support`, review the local report, and attach it to the
[hardware compatibility issue template](https://github.com/DonGeronimo7/mouse-control/issues/new?template=hardware-compatibility.yml). This read-only workflow helps guide future device work; it does not claim the device is already supported.

## Hardware support

Mouse Control does not claim universal hardware support. The matrix below
separates physically validated behavior from expected fallback paths and work
that needs community testing. The detailed, evidence-based record is in
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

| Hardware / backend | Status | Notes |
| --- | --- | --- |
| Logitech G305 | Physically validated | Native HID++ automatic detection; 200–12000 DPI in 50-DPI steps; configured 800/1500/2000/2500/3000 stages; physical DPI events/OSD; 1000/500/250/125 Hz discovery; reconnect, late insertion, and side-button remapping. |
| Logitech HID++ | Architecture available; device testing needed | Feature indexes are discovered live. The G305 is the primary physical reference; other Logitech mice are not claimed as validated. |
| OpenRazer-supported mice | Needs broader validation | Optional backend; physical Razer validation is still needed. |
| Generic HID / evdev mice | Needs broader validation | Exact HID identity and read-only diagnostics plus software remapping; no unvalidated DPI or polling writes. |

## Help test your mouse

Hardware reports—successful as well as unsuccessful ones—build a useful Linux
compatibility database. We especially welcome reports for Logitech, Razer,
SteelSeries, Corsair, Glorious, Roccat/Turtle Beach, ASUS, Cooler Master,
HyperX, generic USB mice, and other manufacturers. This is an invitation to
test, not a claim that those mice are already supported.

1. Install Mouse Control and run `mouse-control setup`.
2. Test button remapping and normal mouse operation.
3. Where relevant, test DPI, DPI notifications, polling, and the background service.
4. Run `mouse-control doctor --report`.
5. Submit the sanitized output with the [Hardware compatibility issue template](https://github.com/DonGeronimo7/mouse-control/issues/new?template=hardware-compatibility.yml).

`mouse-control doctor` is read-only. `mouse-control doctor --report` is meant
for public reports and, as implemented, excludes usernames, home paths, serial
numbers, cache contents, and unrelated USB devices. Review its output before
posting it and remove anything you do not want to share.

## How Mouse Control chooses a backend

Mouse Control uses the best available integration for the selected device and
falls back gracefully when vendor-specific functionality is unavailable:

1. Native HID where a validated protocol driver confidently claims one interface.
2. OpenRazer where its optional daemon/client can confidently recognize it.
3. Generic HID identity with evdev/software remapping when no validated hardware backend is available.

Hardware configuration failures are reported independently and do not prevent
ordinary software remapping from starting.

`mouse-control debug-hid --seconds 10` is the vendor-neutral, read-only
development path. It matches hidraw interfaces to the selected evdev mouse by
bus type and exact VID:PID, prints their report descriptors, and captures input
reports while buttons are pressed. It never sends feature or output reports.
USB HID does not standardize mouse DPI or polling-rate configuration, so a
vendor protocol must be validated before those writes are enabled.

## Optional hardware integrations

### OpenRazer

Logitech users do not need OpenRazer. For Razer hardware, follow the
[upstream installation instructions](https://openrazer.github.io/#download) for
your distribution. OpenRazer is intentionally optional; a missing client,
daemon, or supported capability leaves generic remapping available. Run Mouse
Control as the logged-in desktop user so it can access the same session D-Bus
as the daemon, rather than using `sudo`.

## Device permissions

The Fedora RPM installs
`/usr/lib/udev/rules.d/71-mouse-control-uaccess.rules`. It gives the active
local logind session an ACL for mouse event devices and `/dev/uinput`, while
excluding interfaces also classified as keyboards. It does not change device
modes, add users to `input`, or grant access to every logged-in user. After an
RPM install or upgrade, log out and back in (or reconnect the mouse), then run:

```bash
mouse-control check-permissions
mouse-control setup
```

For a source installation, install the supplied rule and reload udev:

```bash
sudo install -Dm644 src/mouse_control/udev/71-mouse-control-uaccess.rules \
  /etc/udev/rules.d/71-mouse-control-uaccess.rules
sudo udevadm control --reload-rules
```

Do not run `mouse-control run` as root. Keyboard capture is deliberately not
granted by this rule because reading keyboard events can expose keystrokes; the
wizard retains manual `KEY_*` entry when desktop policy does not permit capture.

## Logitech G305 and HID++ notes

The G305 is the reference hardware for the currently validated HID++ DPI
behavior. Its two routes are deliberately distinct:

- Adjustable DPI query feature `0x2201`: dynamically discovered feature-table index.
- Physically observed passive DPI notification: Onboard Profiles event index `0x07` on the G305.

These indexes are device-specific and must not be collapsed into one mapping.
Normal `mouse-control run` owns one selected hidraw interface, performs live
HID++ ROOT discovery, and multiplexes replies with unsolicited events. Live
feature discovery is authoritative. An unsupported or ambiguous device retains
ordinary remapping without hardware writes.

`mouse-control debug-dpi` is the explicit diagnostic workflow. It can
discover HID++ features and observe the
physical DPI button. It does not change firmware button mappings, and normal
runtime does not import Solaar or add it as a dependency. For full diagnostic
details, see [RELEASE_NOTES.md](RELEASE_NOTES.md).

## Configuration and behavior

The configuration is saved at `~/.config/mouse-control/config.toml`. Supported
mapping actions are `passthrough`, `disable`, `mouse:BTN_*`, `key:KEY_*`, and
`chord:KEY_*+KEY_*` (with two or more keys).
The configuration can be edited by hand.

To test a source checkout with an isolated config, without changing the normal
installed configuration, run:

```bash
PYTHONPATH=src python3 -m mouse_control.cli run --config /tmp/mouse-control-test.toml
```

For example, in `[remap]`:

```toml
BTN_EXTRA = "chord:KEY_LEFTCTRL+KEY_LEFTSHIFT+KEY_S"
```

The chord stays held while the mouse button is held.

When supported by a native driver, setup uses project defaults of 800, 1500, 2000,
2500, and 3000 DPI, selects 800 DPI as active/default, and chooses the highest
reported polling rate. Hardware that lacks enough programmable slots or rejects
values reports the limitation rather than pretending that settings were applied.
Generic devices do not receive hardware DPI writes.

Mouse Control deliberately excludes RGB/lighting control, multiple profiles,
and hardware features beyond DPI and polling.

## Development and validation

```bash
python3 -m pytest -q
python3 -m compileall -q src tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for bug reports, hardware reports, and
pull requests. Packaging notes are available for [AppImage](packaging/appimage/README.md),
[Debian](debian/control), and [RPM](mouse-control.spec) builds.

## License

Mouse Control is licensed under the GNU General Public License, version 3 or
later. See [LICENSE](LICENSE).
