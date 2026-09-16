"""Command-line interface for mouse-control."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading

from .config import (DEFAULT_DPI, DEFAULT_DPI_STAGES, generate_config, get_config_path,
                     load_config, merge_setup_config, save_config)
from .discovery import MouseDevice, get_mouse_devices, select_mouse_device
from .hardware import (DesiredHardwareState, HardwareBackend, HardwareError,
                       HardwareSupervisor, get_backend)
from .hardware.generic import GenericBackend
from .remapper import DpiCycler, MouseRemapper
from .notifications import DpiMonitorSupervisor
from .battery import BatteryMonitorSupervisor
from .hidpp_debug import debug_dpi
from .generic_hid import capture_input_reports, discover_hid_devices
from .wizard import ButtonCaptureError, map_mouse_buttons
from .setup_flow import (SetupChoices, discover_choices, restore_dpi, dpi_screen,
                         polling_screen, review_screen)

from .service import (install_service, is_service_active, start_service, stop_service,
                      restart_service, status_service, ServiceNotInstalled)
from .permissions import permission_report
from .doctor import doctor_fix, print_doctor
from . import __version__
from .branding import print_banner, style
from .updater import run_update


LOG = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mouse-control")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("setup", help="run the interactive setup wizard")
    run_parser = sub.add_parser("run", help="apply the saved configuration")
    run_parser.add_argument("--config", type=Path,
                            help="use an explicit configuration file instead of the default")
    sub.add_parser("show-config", help="print the active configuration path")
    sub.add_parser("check-permissions", help="check mouse and uinput access for this session")
    sub.add_parser("debug-dpi", help="discover Logitech HID++ capabilities and capture reports")
    debug_hid = sub.add_parser(
        "debug-hid", help="read-only generic HID discovery and report capture"
    )
    debug_hid.add_argument(
        "--seconds", type=float, default=10.0,
        help="seconds to capture each matching hidraw interface (default: 10)",
    )
    doctor = sub.add_parser("doctor", help="read-only environment and hardware diagnostics")
    doctor.add_argument("--report", action="store_true", help="format a privacy-safe compatibility report")
    doctor.add_argument("--fix", action="store_true", help="offer safe dependency-installation guidance")
    support = sub.add_parser("support", help="create a privacy-safe hardware support report")
    support.add_argument("--guided", action="store_true", help="also capture one optional button press")

    sub.add_parser("install-service", help="install and enable the systemd user service")
    sub.add_parser("start", help="start the background service")
    sub.add_parser("stop", help="stop the background service")
    sub.add_parser("restart", help="restart the background service")
    sub.add_parser("status", help="show the background service status")
    update = sub.add_parser("update", help="check for and safely install an update")
    update.add_argument("--check", action="store_true", help="only check whether an update is available")
    update.add_argument("--yes", action="store_true", help="do not ask before updating")

    return parser


def _default_mappings() -> dict[str, str]:
    return {
        "BTN_LEFT": "passthrough",
        "BTN_RIGHT": "passthrough",
        "BTN_MIDDLE": "passthrough",
    }


def _load_setup_config() -> dict[str, object]:
    """Load an existing configuration once; absence means first-run defaults."""
    path = get_config_path()
    if not path.exists():
        return {}
    existing = load_config(path)
    if not isinstance(existing, dict):
        raise ValueError("Existing configuration is invalid")
    return existing


def _initial_mappings(existing: dict[str, object]) -> dict[str, str]:
    """Keep an existing remap table intact until the wizard edits a button."""
    if "remap" not in existing:
        return _default_mappings()
    mappings = existing["remap"]
    if not isinstance(mappings, dict) or not all(
            isinstance(button, str) and isinstance(action, str)
            for button, action in mappings.items()):
        raise ValueError("Existing [remap] configuration is invalid")
    return dict(mappings)


def _initial_choices(existing: dict[str, object]) -> SetupChoices:
    dpi = existing.get("dpi", {})
    polling = existing.get("polling", {})
    if not isinstance(dpi, dict) or not isinstance(polling, dict):
        raise ValueError("Existing DPI or polling configuration is invalid")
    stages = dpi.get("stages", DEFAULT_DPI_STAGES)
    active = dpi.get("active", DEFAULT_DPI)
    if (not isinstance(stages, list) or
            any(not isinstance(value, int) or value <= 0 for value in stages) or
            not isinstance(active, int) or active <= 0):
        raise ValueError("Existing DPI configuration is invalid")
    rate = polling.get("rate_hz")
    if rate is not None and (not isinstance(rate, int) or rate <= 0):
        raise ValueError("Existing polling configuration is invalid")
    return SetupChoices(stages=list(stages), active_dpi=active, polling_rate=rate,
                        mappings=_initial_mappings(existing))


def debug_hid(seconds: float = 10.0) -> int:
    """Identify matching HID interfaces and capture reports without writes."""
    if seconds <= 0:
        print("--seconds must be greater than zero", file=sys.stderr)
        return 2
    mice = get_mouse_devices()
    if not mice:
        print("No mouse devices found. Check input permissions.", file=sys.stderr)
        return 1
    selected = select_mouse_device(mice)
    if selected is None:
        return 0
    identity = (f"{selected.vendor:04x}:{selected.product:04x}"
                if selected.vendor is not None and selected.product is not None
                else "unavailable")
    print(f"Selected: {selected.name} [{identity}]")
    interfaces = discover_hid_devices(selected)
    if not interfaces:
        print("No matching hidraw interfaces were found.", file=sys.stderr)
        return 1
    print(f"Matching hidraw interfaces: {len(interfaces)}")
    for interface in interfaces:
        print(f"\n{interface.path}: {interface.name}")
        print(f"  physical path: {interface.phys or 'unavailable'}")
        print(f"  report descriptor ({len(interface.report_descriptor)} bytes): "
              f"{interface.report_descriptor.hex(' ')}")
        print(f"  capturing input reports read-only for {seconds:g} seconds; "
              "press the DPI and extra buttons now")
        seen: set[bytes] = set()
        try:
            capture_input_reports(interface, seconds, lambda report: seen.add(report))
        except PermissionError as exc:
            print(f"  permission denied: {exc}", file=sys.stderr)
            continue
        except OSError as exc:
            print(f"  capture unavailable: {exc}", file=sys.stderr)
            continue
        if seen:
            for report in sorted(seen):
                print(f"  input: {report.hex(' ')}")
        else:
            print("  no input reports observed")
    print("\nNo HID feature or output reports were sent.")
    return 0


def run_support(*, guided: bool = False) -> int:
    """Create an explicitly requested local report without hardware writes."""
    from .support import capture_button, probe, render_report

    print_banner()
    print(style("Mouse Control Hardware Support", "purple") + "\n" + "─" * 31)
    mice = get_mouse_devices()
    if not mice:
        print("No mouse devices were detected. Check that your mouse is connected and try again.")
        return 1
    print("\nSelect the mouse you're having trouble with:")
    for index, mouse in enumerate(mice, 1):
        print(f"  {index}. {mouse.name}")
    try:
        selected = mice[int(input("\n> ").strip()) - 1]
    except (ValueError, IndexError, EOFError, KeyboardInterrupt):
        print("\nSupport report cancelled.")
        return 0
    print(f"\nChecking {selected.name}...")
    report = probe(selected, get_backend(selected))
    if guided:
        print("\nOptional button test: press the BACK or FORWARD button now (10 seconds)...")
        event = capture_button(selected)
        report["guided"]["Requested action"] = "Press a side button"
        report["guided"]["Detected event"] = event or "No event detected"
    try:
        answer = input("Create support report? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"
    if answer not in ("", "y", "yes"):
        print("Support report cancelled.")
        return 0
    slug = "".join(char.lower() if char.isalnum() else "-" for char in selected.name).strip("-") or "mouse"
    destination = Path.home() / f"mouse-control-{slug}-report.txt"
    try:
        destination.write_text(render_report(report), encoding="utf-8")
    except OSError as exc:
        print(f"Could not save support report: {exc}", file=sys.stderr)
        return 1
    print(f"Report saved: {destination}\nPlease attach this report to a GitHub issue.")
    return 0


def _choose_default_dpi(backend: HardwareBackend, device: MouseDevice) -> tuple[list[int], int]:
    """Use the project's preferred DPI stages, adapting only when hardware rejects them."""
    try:
        supported = backend.get_dpi_values(device)
    except HardwareError as exc:
        print(f"{backend.name} DPI query unavailable: {exc}")
        return DEFAULT_DPI_STAGES, DEFAULT_DPI

    print(f"\nDefault DPI stages: {', '.join(map(str, DEFAULT_DPI_STAGES))}")
    if supported:
        print(f"Mouse-reported DPI values: {', '.join(map(str, supported))}")
        unsupported = [value for value in DEFAULT_DPI_STAGES if value not in supported]
        if unsupported:
            print(
                "Note: the mouse does not currently report all preferred stages; "
                "available hardware slots will be used where possible."
            )
    return DEFAULT_DPI_STAGES, DEFAULT_DPI

def _select_max_polling_rate(backend: HardwareBackend, device: MouseDevice) -> int | None:
    """Select the highest reported rate for subsequent application."""
    try:
        supported = backend.get_polling_rates(device)
        current = backend.get_polling_rate(device)
    except HardwareError as exc:
        print(f"{backend.name} polling-rate query unavailable: {exc}")
        return None

    if not supported:
        print(f"No supported polling rates were reported by {backend.name}.")
        return current

    maximum = max(supported)
    print(f"Supported polling rates: {', '.join(str(value) + ' Hz' for value in supported)}")
    print(f"Selected maximum polling rate for setup: {maximum} Hz")
    print("The wizard will report separately whether the hardware write is verified.")
    return maximum

def _ask_enable_service() -> bool:
    """Ask whether mouse-control should start automatically at login."""
    while True:
        answer = input(
            "\nEnable Mouse Control to run automatically at login? [Y/n]: "
        ).strip().lower()

        if answer in ("", "y", "yes"):
            return True

        if answer in ("n", "no"):
            return False

        print("Please enter Y or N.")


def run_setup_wizard() -> int:
    print_banner()
    print(style("Mouse Control Setup Wizard", "purple"))
    # Read-only readiness information: optional backends never block remapping.
    print("Native HID discovery enabled; unsupported mice retain generic remapping.")
    was_active = is_service_active()
    service_restored = False
    selected = None
    backend = None
    choices = None
    saved = False
    if was_active:
        print("Mouse Control background service is running.")
        print("Temporarily stopping it for setup...")
        try:
            stop_service()
        except Exception as exc:
            print(f"Could not stop the background service: {exc}", file=sys.stderr)
            return 1

    try:
        mice = get_mouse_devices()
        if not mice:
            print("No mouse devices found. Check input permissions.")
            return 1

        selected = select_mouse_device(mice)
        if selected is None:
            print("Setup cancelled.")
            return 0

        print(f"\nSelected: {selected.name}")
        backend = get_backend(selected)
        try:
            hardware_name = backend.get_device_name(selected)
            if hardware_name and hardware_name != selected.name:
                identity = (f" [{selected.vendor:04x}:{selected.product:04x}]"
                            if selected.vendor is not None and selected.product is not None else "")
                print(f"Hardware name: {hardware_name}{identity}")
        except HardwareError as exc:
            logging.info("Hardware name lookup unavailable: %s", exc)
        existing_config = _load_setup_config()
        choices = _initial_choices(existing_config)
        discover_choices(backend, selected, choices)
        page = 'buttons'
        review_return = False
        finished = False
        while not finished:
            if page == 'buttons':
                print('\nButton mappings: press buttons to configure; Ctrl+C ends capture.')
                print('[Enter] Configure buttons  [S] Skip/keep mappings  [B] Back  [Q] Cancel setup')
                answer = input('> ').strip().lower()
                if answer == 'q':
                    break
                if answer == 'b':
                    replacement = select_mouse_device(mice)
                    if replacement is None:
                        break
                    if replacement != selected:
                        restore_dpi(backend, selected, choices.original_dpi)
                        selected = replacement
                        backend = get_backend(selected)
                        choices = _initial_choices(existing_config)
                        discover_choices(backend, selected, choices)
                        review_return = False
                    continue
                if answer not in ('', 'e', 'edit', 's', 'skip'):
                    print('Press Enter, S, B, or Q.')
                    continue
                if answer in ('', 'e', 'edit'):
                    choices.mappings.update(map_mouse_buttons(selected.path))
                page = 'review' if review_return else 'dpi'
            elif page == 'dpi':
                action = dpi_screen(backend, selected, choices)
                if action == 'q':
                    break
                page = ('review' if review_return else 'buttons') if action == 'b' else 'review' if review_return else 'polling'
            elif page == 'polling':
                action = polling_screen(choices)
                if action == 'q':
                    break
                page = ('review' if review_return else 'dpi') if action == 'b' else 'review' if review_return else 'service'
            elif page == 'service':
                print('\nEnable Mouse Control at login?')
                print('[Enter/Y] Yes  [N] No  [B] Back  [Q] Cancel setup')
                answer = input('> ').strip().lower()
                if answer == 'q':
                    break
                if answer == 'b':
                    page = 'polling'
                elif answer in ('', 'y', 'yes', 'n', 'no'):
                    choices.enable_service = answer not in ('n', 'no')
                    page = 'review'
                else:
                    print('Choose Y, N, B, or Q.')
            else:
                action = review_screen(selected, choices)
                if action == 'q':
                    break
                if action == 'b':
                    review_return = False
                    page = 'service'
                elif action in ('1', '2', '3'):
                    review_return = True
                    page = {'1': 'dpi', '2': 'polling', '3': 'buttons'}[action]
                else:
                    finished = True
        if not finished:
            restore_dpi(backend, selected, choices.original_dpi)
            print('Setup cancelled; the existing configuration was not changed.')
            return 0
        dpi_stages = choices.stages
        active_dpi = choices.active_dpi
        polling_rate_hz = choices.polling_rate
        mappings = choices.mappings
        enable_service = choices.enable_service
        if any(not isinstance(v, int) or v <= 0 for v in dpi_stages):
            raise ValueError('Invalid DPI stages')
        if (choices.dpi_changed and choices.dpi_values and
                any(v not in choices.dpi_values for v in dpi_stages)):
            raise ValueError('One or more DPI stages are unsupported by this mouse')
        if (choices.polling_changed and polling_rate_hz is not None and
                polling_rate_hz not in choices.polling_rates):
            raise ValueError('Polling rate is unsupported by this mouse')
        content = merge_setup_config(existing_config, selected, mappings=mappings,
                                     dpi_stages=dpi_stages, active_dpi=active_dpi,
                                     polling_rate_hz=polling_rate_hz)
        _apply_hardware(
            backend, selected, dpi_stages if choices.dpi_changed else [],
            active_dpi if choices.dpi_changed else 0,
            polling_rate_hz if choices.polling_changed else None, setup=True
        )
        path = save_config(content)
        saved = True
        print(f"\nConfiguration saved to: {path}")

        if enable_service:
            try:
                install_service()
                service_restored = True
                print("Mouse Control background service enabled and started.")
            except Exception as exc:
                print(f"Warning: could not enable background service: {exc}")
                print("Your mouse configuration was still saved successfully.")
        else:
            print(
                "Background service not enabled. "
                "You can enable it later with: mouse-control install-service"
            )
        return 0
    except ButtonCaptureError:
        print("Setup failed; the existing configuration was not changed.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nSetup cancelled; the existing configuration was not changed.")
        return 1
    except Exception as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        print("The existing configuration was not changed.", file=sys.stderr)
        return 1
    finally:
        if not saved and selected is not None and backend is not None and choices is not None:
            restore_dpi(backend, selected, choices.original_dpi)
        if was_active and not service_restored:
            try:
                restart_service()
            except Exception as exc:
                print(f"Warning: could not restart the background service: {exc}",
                      file=sys.stderr)


def _apply_hardware(backend: HardwareBackend, device: MouseDevice,
                    stages: list[int], active_dpi: int, polling_rate_hz: int | None,
                    *, setup: bool = False) -> None:
    """Apply independent capabilities; a hardware failure never blocks remapping."""
    try:
        if active_dpi > 0 and backend.supports_dpi(device):
            applied_directly = False
            if backend.supports_dpi_stages(device):
                slot = backend.apply_dpi_stages(device, stages, active_dpi)
                if slot is not None:
                    print(f"Hardware DPI stages applied; {active_dpi} DPI is active/default "
                          f"through {backend.name} (resolution {slot}).")
                elif setup:
                    print("Warning: the mouse did not expose a resolution slot for the requested active DPI.")
                else:
                    backend.set_dpi(device, active_dpi)
                    applied_directly = True
            else:
                backend.set_dpi(device, active_dpi)
                applied_directly = True
            if applied_directly:
                actual = backend.get_dpi(device)
                matches = (actual == active_dpi or
                           isinstance(actual, tuple) and actual[0] == active_dpi and
                           actual[1] in (0, active_dpi))
                if actual is None:
                    print(f"Hardware DPI requested at {active_dpi} through {backend.name}; "
                          "readback is unavailable.")
                elif matches:
                    print(f"Hardware DPI set and verified at {active_dpi} through {backend.name}.")
                else:
                    raise HardwareError(
                        f"DPI verification requested {active_dpi}, read {actual}")
        elif setup and active_dpi > 0:
            print(f"DPI control is unsupported by {backend.name}; hardware DPI was unchanged.")
    except HardwareError as exc:
        logging.warning("Could not apply %s DPI settings: %s", backend.name, exc)

    try:
        if polling_rate_hz is not None and backend.supports_polling_rate_writes(device):
            backend.set_polling_rate(device, int(polling_rate_hz))
            actual = backend.get_polling_rate(device)
            if actual is None:
                print(f"Hardware polling rate requested at {polling_rate_hz} Hz through "
                      f"{backend.name}; readback is unavailable.")
            elif actual == int(polling_rate_hz):
                print(f"Hardware polling rate set and verified at {actual} Hz through {backend.name}.")
            else:
                raise HardwareError(
                    f"polling verification requested {polling_rate_hz} Hz, read {actual} Hz")
        elif setup and polling_rate_hz is not None:
            print(f"Polling-rate writes are unsupported by {backend.name}; hardware was unchanged.")
    except (HardwareError, ValueError, TypeError) as exc:
        logging.warning("Could not apply %s polling settings: %s", backend.name, exc)


def _resolve_runtime_device(configured: MouseDevice) -> MouseDevice:
    """Resolve reconnect identity without guessing among matching devices."""
    mice = get_mouse_devices()
    by_path = [mouse for mouse in mice
               if os.path.realpath(mouse.path) == os.path.realpath(configured.path) and
               (configured.vendor is None or mouse.vendor == configured.vendor) and
               (configured.product is None or mouse.product == configured.product) and
               (configured.bustype is None or mouse.bustype == configured.bustype)]
    if len(by_path) == 1:
        return by_path[0]
    if configured.vendor is None or configured.product is None:
        return configured
    candidates = [mouse for mouse in mice
                  if mouse.vendor == configured.vendor and
                  mouse.product == configured.product and
                  (configured.bustype is None or mouse.bustype == configured.bustype)]
    if configured.phys:
        physical = [mouse for mouse in candidates if mouse.phys == configured.phys]
        if len(physical) == 1:
            return physical[0]
    return candidates[0] if len(candidates) == 1 else configured


def run_from_config(path: Path | None = None) -> int:
    try:
        config = load_config(path)
    except OSError as exc:
        print(f"Could not read configuration: {exc}", file=sys.stderr)
        return 1
    device = config.get("device", {})
    mappings = config.get("remap", {})
    event_path = device.get("event_path")
    if not event_path:
        print("Configuration is missing [device].event_path", file=sys.stderr)
        return 1

    dpi_config = config.get("dpi", {})
    try:
        dpi_stages = [int(value) for value in dpi_config.get("stages", DEFAULT_DPI_STAGES)]
        active_dpi = int(dpi_config.get("active", DEFAULT_DPI))
        if active_dpi <= 0:
            raise ValueError("Active DPI must be greater than zero")
    except (ValueError, TypeError) as exc:
        logging.warning("Invalid DPI configuration; skipping DPI settings: %s", exc)
        dpi_stages, active_dpi = [], 0
    polling_rate_hz = config.get("polling", {}).get("rate_hz")

    mouse = next((m for m in get_mouse_devices()
                  if os.path.realpath(m.path) == os.path.realpath(event_path)), None)
    configured_mouse = MouseDevice(
        str(device.get("name", "")), event_path,
        phys=str(device.get("phys", "") or ""),
        vendor=device.get("vendor") if isinstance(device.get("vendor"), int) else None,
        product=device.get("product") if isinstance(device.get("product"), int) else None,
        bustype=device.get("bustype") if isinstance(device.get("bustype"), int) else None,
    )
    monitor = None
    dpi_cycler = None
    shutdown_event = threading.Event()
    enabled = config.get("notifications", {}).get("dpi_changes", True)
    if isinstance(enabled, bool):
        notifications_enabled = enabled
    else:
        logging.warning("Invalid notifications.dpi_changes; using enabled default")
        notifications_enabled = True
    cycle_device = mouse or configured_mouse
    g305_hidpp = (cycle_device.vendor == 0x046d and cycle_device.product == 0x4074 and
                   cycle_device.bustype in (None, 3))
    hardware_device = mouse or configured_mouse
    initial_backend = get_backend(hardware_device, log_failures=mouse is not None)
    hardware = HardwareSupervisor(
        initial_backend, hardware_device,
        lambda selected: get_backend(selected, log_failures=False),
        DesiredHardwareState(active_dpi=active_dpi,
                             dpi_stages=tuple(dpi_stages),
                             polling_rate_hz=polling_rate_hz),
        device_resolver=_resolve_runtime_device,
        discovery_pending=mouse is None or isinstance(initial_backend, GenericBackend))
    if mouse is not None:
        identity = (f"{mouse.vendor:04x}:{mouse.product:04x}"
                    if mouse.vendor is not None and mouse.product is not None
                    else "identity unavailable")
        LOG.info("Selected mouse: %s (%s, %s)", mouse.name, mouse.path, identity)
        LOG.info("Selected hardware backend: %s (%s)", initial_backend.name,
                 type(initial_backend).__name__)
    hardware.reconcile()
    if "dpi-cycle" in mappings.values() or g305_hidpp:
        dpi_cycler = DpiCycler(hardware, hardware_device, dpi_stages, active_dpi,
                               notifications_enabled)

    if notifications_enabled or g305_hidpp:
        monitor_kwargs = ({"dpi_cycler": dpi_cycler,
                           "notifications_enabled": notifications_enabled}
                          if g305_hidpp else {})
        monitor = DpiMonitorSupervisor(hardware, hardware_device,
                                       lambda _device: hardware,
                                       dpi_stages, active_dpi, shutdown_event, **monitor_kwargs)
        if dpi_cycler is not None:
            dpi_cycler.notifier = monitor
        LOG.info("DPI notification monitor: %s", type(monitor).__name__)
    else:
        LOG.info("DPI notification monitor: disabled")

    # Battery is a separate optional capability.  Its failures must remain
    # invisible to the evdev/uinput remapper.
    battery_monitor = BatteryMonitorSupervisor(
        hardware, hardware_device, lambda _selected: hardware, shutdown_event)

    if monitor is not None:
        LOG.info("Starting DPI notification monitor")
        monitor.start()
    battery_monitor.start()
    try:
        MouseRemapper(event_path, mappings, shutdown_event, dpi_cycler,
                      target_device=mouse or configured_mouse).run()
    finally:
        if monitor is not None:
            monitor.stop()
        battery_monitor.stop()
        hardware.close()
    return 0


def _run_service_action(action: str) -> int:
    """Run a service action with the same friendly error boundary as the CLI."""
    actions = {
        "1": ("Status", status_service),
        "2": ("Start", start_service),
        "3": ("Restart", restart_service),
        "4": ("Stop", stop_service),
        "5": ("Install and enable", install_service),
    }
    label, operation = actions[action]
    try:
        result = operation()
        return int(result) if isinstance(result, int) else 0
    except (ServiceNotInstalled, OSError, subprocess.CalledProcessError) as exc:
        print(f"Service {label.lower()} unavailable: {exc}", file=sys.stderr)
        return 1


def _service_menu(input_func=input) -> int:
    print("\nService controls")
    print("  1. Service status")
    print("  2. Start service")
    print("  3. Restart service")
    print("  4. Stop service")
    print("  5. Install and enable service")
    print("  B. Back")
    try:
        choice = input_func("Service action: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if choice in {"b", "", "q"}:
        return 0
    if choice in {"1", "2", "3", "4", "5"}:
        return _run_service_action(choice)
    print("Please choose a numbered service action or B to go back.")
    return 0


def run_home_screen(*, input_func=input, columns: int | None = None) -> int:
    """Present a lightweight, keyboard-first terminal home screen."""
    print_banner(columns=columns)
    try:
        active = is_service_active()
    except (OSError, subprocess.CalledProcessError):
        active = None
    status = "Running" if active else "Not running" if active is False else "Unknown"
    status_style = "success" if active else "muted" if active is False else "warning"
    print(f"\nService: {style(status, status_style)}")
    print("\n  1. Configure mouse")
    print("  2. Button remapping")
    print("  3. DPI & polling")
    print("  4. Hardware support")
    print("  5. Service controls")
    print("  6. Diagnostics")
    print("  7. Help / About")
    print("  Q. Quit")

    routes = {
        "1": run_setup_wizard,
        "2": run_setup_wizard,
        "3": run_setup_wizard,
        "4": run_support,
        "6": print_doctor,
    }
    while True:
        try:
            choice = input_func("\nChoose an option: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            return 0
        if choice in {"q", "quit"}:
            print("Goodbye.")
            return 0
        if choice == "5":
            _service_menu(input_func)
            return 0
        if choice == "7":
            _build_parser().print_help()
            return 0
        route = routes.get(choice)
        if route is not None:
            return int(route() or 0)
        print("Please choose 1–7 or Q to quit.")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    supplied_argv = sys.argv[1:] if argv is None else argv
    if not supplied_argv:
        if sys.stdin.isatty() and sys.stdout.isatty():
            return run_home_screen()
        _build_parser().print_help()
        return 0

    args = _build_parser().parse_args(supplied_argv)

    if args.command == "setup":
        return run_setup_wizard()

    if args.command == "run":
        return run_from_config(args.config)

    if args.command == "show-config":
        print(get_config_path())
        return 0

    if args.command == "check-permissions":
        return permission_report()

    if args.command == "debug-dpi":
        return debug_dpi()

    if args.command == "debug-hid":
        return debug_hid(args.seconds)

    if args.command == "doctor":
        if args.fix:
            return doctor_fix()
        return print_doctor(report=args.report)

    if args.command == "support":
        return run_support(guided=args.guided)

    if args.command == "update":
        return run_update(check=args.check, assume_yes=args.yes)

    try:
        if args.command == "install-service":
            install_service()
            return 0

        if args.command == "start":
            start_service()
            return 0

        if args.command == "stop":
            stop_service()
            return 0

        if args.command == "restart":
            restart_service()
            return 0

        if args.command == "status":
            return status_service()
    except (ServiceNotInstalled, OSError, subprocess.CalledProcessError) as exc:
        print(f"mouse-control service: {exc}", file=sys.stderr)
        return 1

    _build_parser().print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
