Name:           mouse-control
Version:        0.8.2
Release:        1%{?dist}
Summary:        Mouse remapping with optional hardware backends
License:        GPL-3.0-or-later
Source0:        mouse_control-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-build
BuildRequires:  python3-setuptools >= 77.0.3
BuildRequires:  python3-wheel
BuildRequires:  python3-pytest
BuildRequires:  python3-evdev
BuildRequires:  python3-dbus-next
BuildRequires:  python3-packaging
BuildRequires:  pyproject-rpm-macros
BuildRequires:  systemd-rpm-macros
BuildRequires:  desktop-file-utils
# The wheel installer is kept from writing bytecode below; avoid recreating it
# during RPM's post-install processing so build-time caches are not shipped.
%undefine py_auto_byte_compile
Requires:       python3-evdev
Requires:       python3-dbus-next
Requires:       python3-packaging
Requires:       systemd-udev

%description
A command-line mouse button remapper using evdev and uinput, with optional
hardware DPI configuration through native HID or OpenRazer. Includes an
interactive setup wizard and commands for managing a systemd user service.

%prep
%autosetup -n mouse_control-%{version}

%build
%pyproject_wheel

%install
export PYTHONDONTWRITEBYTECODE=1
export PIP_NO_COMPILE=1
%pyproject_install
install -Dpm 0644 src/mouse_control/udev/71-mouse-control-uaccess.rules \
  %{buildroot}%{_udevrulesdir}/71-mouse-control-uaccess.rules
install -Dpm 0644 packaging/appimage/mouse-control.desktop \
  %{buildroot}%{_datadir}/applications/mouse-control.desktop
for size in 512 256 128 64 48 32; do
  install -Dpm 0644 assets/icons/hicolor/${size}x${size}/apps/mouse-control.png \
    %{buildroot}%{_datadir}/icons/hicolor/${size}x${size}/apps/mouse-control.png
done
desktop-file-validate %{buildroot}%{_datadir}/applications/mouse-control.desktop
%pyproject_save_files mouse_control
# pip records bytecode even when it is not a distributable source file.  Remove
# it only after the generated file manifest has been created, then omit it from
# that manifest as well.
find %{buildroot}%{python3_sitelib} -type d -name __pycache__ -prune -exec rm -rf {} +
sed -i '\|__pycache__|d' %{pyproject_files}

%check
/usr/bin/python3 -m pytest -q tests
/usr/bin/python3 -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=%{buildroot}%{python3_sitelib} \
  %{buildroot}%{_bindir}/mouse-control --help
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=%{buildroot}%{python3_sitelib} \
  %{buildroot}%{_bindir}/mouse-control-discover --help

%files -f %{pyproject_files}
%license LICENSE
%doc README.md CHANGELOG.md
%doc docs/COMPATIBILITY.md
%{_bindir}/mouse-control
%{_bindir}/mouse-control-discover
%{_udevrulesdir}/71-mouse-control-uaccess.rules
%{_datadir}/applications/mouse-control.desktop
%{_datadir}/icons/hicolor/*/apps/mouse-control.png

%changelog
* Tue Sep 15 2026 Marc-Anthony Geronimo - 0.8.2-1
- Stabilize native hardware control and reconnect fallback behavior.
- Preserve remaps and setup configuration while hardware options are reviewed.

* Mon Sep 14 2026 Marc-Anthony Geronimo - 0.8.1-1
- Add revisitable wizard navigation, live numeric DPI testing, and safe rollback.
- Show readable polling rates and select only when writes are supported.
- Persist accepted DPI stages for runtime cycling.

* Mon Sep 14 2026 Marc-Anthony Geronimo - 0.8.0-1
- Add held keyboard chord bindings with shared-modifier and disconnect cleanup.
- Allow an explicit configuration file for isolated runtime testing.

* Mon Sep 14 2026 Marc-Anthony Geronimo - 0.7.11-1
- Verify RPM updates from the installed package version after DNF completes.
- Make `mouse-control update --yes` non-interactive for DNF upgrades and fallback installs.

* Mon Sep 14 2026 Marc-Anthony Geronimo - 0.7.10-1
- Keep the battery tray visible through transient HID++ read timeouts.
- Remove the redundant device name from the tray menu and refresh terminal branding.

* Mon Sep 14 2026 Marc-Anthony Geronimo - 0.7.9-1
- Bundle a self-contained Python 3.12 runtime in the AppImage.
- Prevent host Python ABI mismatches with native extensions such as evdev.

* Mon Sep 14 2026 Marc-Anthony Geronimo - 0.7.8-1
- Verify installed RPM and DEB versions before reporting updater success.
- Fall back to the validated GitHub package when native repositories do not
  actually upgrade a direct-release installation.

* Mon Sep 14 2026 Marc-A. Geronimo - 0.7.7-1
- Polish the terminal launcher mark and strengthen icon visibility at small sizes.

* Mon Sep 14 2026 Marc-A. Geronimo - 0.7.6-1
- Fix Arch build dependencies for the updater release.

* Sun Sep 13 2026 Marc-A. Geronimo - 0.7.5-1
- Add the safe cross-distribution update command and package its version
  comparison dependency.

* Sun Sep 13 2026 Marc-A. Geronimo - 0.7.4-1
- Release community hardware support reporting with a local, read-only workflow.

* Sun Sep 13 2026 Marc-A. Geronimo - 0.7.3-1
- Add the on-demand, read-only mouse-control support report workflow for
  community hardware testing. Reports remain local and include optional guided
  button capture plus selected-mouse HID descriptor topology only.
- Add the optional StatusNotifierItem battery monitor with a live icon,
  tooltip, and standards-based DBusMenu battery details.

* Sun Sep 13 2026 Marc-A. Geronimo - 0.7.2-1
- Release the native Logitech HID++ stabilization milestone.
- Add reconnect-safe backend rediscovery and service control commands.

* Sat Sep 12 2026 Marc-A. Geronimo - 0.6.9-1
- Prepare Linux-wide community hardware testing with doctor diagnostics,
  privacy-safe reports, Debian/Arch/AppImage packaging definitions, issue
  templates, and a compatibility matrix.
- Preserve the validated passive G305 HID++ implementation unchanged.

* Fri Sep 11 2026 Marc-A. Geronimo - 0.6.3-1
- Add controlled generic Logitech HID++ capability and device-name discovery.
- Keep normal runtime passive and preserve independent G305 DPI query/event routes.
- Validate notifications on Logitech G305 046d:4074 without changing firmware mappings.

* Fri Sep 11 2026 Marc-A. Geronimo - 0.5.0-1
- Synchronize the validated G305 HID++ DPI monitor and Freedesktop notifications.
- Preserve safe setup/service recovery, configurable DPI stages and maximum polling.
- Package the scoped G305 hidraw uaccess rule and current 89-test source tree.

* Fri Sep 11 2026 Marc-A. Geronimo - 0.4.2-3
- Notify through Freedesktop D-Bus when a hardware backend reports a DPI change.
- Submit one independent DPI notification per physical press and keep notification failures nonfatal.

* Fri Sep 11 2026 Marc-A. Geronimo - 0.4.2-2
- License the project under GPL-3.0-or-later.
- Install active-session uaccess rules for mouse event devices and uinput.
- Use the Fedora pyproject build macros.

* Thu Sep 10 2026 Marc-A. Geronimo - 0.4.2-1
- Add physical keyboard/media-key capture with manual-entry fallback.
- Add extensible hardware backend selection.
- Preserve G305 DPI stages, polling defaults, config and service behavior.
- Keep OpenRazer optional and hardware failures nonfatal to remapping.
- Include tuple-alias fix in source; remove redundant packaging patch.
- Synchronize Python version metadata; run 38 tests and compile checks.

* Thu Sep 10 2026 Marc-A. Geronimo - 0.3.3-1
- Update package and Python version metadata to 0.3.3.

* Thu Sep 10 2026 Marc-A. Geronimo - 0.2.5-1
- Package the existing utility, including its user service controls.
- Accept tuple button aliases returned by evdev.
