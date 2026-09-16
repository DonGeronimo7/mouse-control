"""Shared hardware lifecycle and reconnect reconciliation regressions."""

from unittest.mock import MagicMock

from mouse_control.discovery import MouseDevice
from mouse_control.hardware import (DesiredHardwareState, HardwareBackend,
                                    HardwareSupervisor)
from mouse_control.hardware.generic import GenericBackend
from mouse_control.hardware.capabilities import BatteryState, DpiState
from mouse_control.remapper import DpiCycler


G305 = MouseDevice("G305", "/dev/input/test", vendor=0x046d,
                   product=0x4074, bustype=3)


def backend(name="Native HID", *, dpi=800, rate=1000):
    live = {"dpi": dpi, "rate": rate}
    target = MagicMock(spec=HardwareBackend)
    target.name = name
    target.supports_device.return_value = True
    target.supports_polling_rate_writes.return_value = True
    target.get_polling_rate.side_effect = lambda _device: live["rate"]
    target.set_polling_rate.side_effect = lambda _device, value: live.update(rate=value)
    target.supports_dpi_stages.return_value = False
    target.supports_dpi.return_value = True
    target.get_dpi.side_effect = lambda _device: live["dpi"]
    def set_dpi(_device, value):
        live["dpi"] = value
        return DpiState(value, confirmed=True)
    target.set_dpi.side_effect = set_dpi
    target.supports_dpi_events.return_value = True
    target.supports_battery.return_value = True
    target.get_battery_state.return_value = BatteryState(percentage=90)
    return target


def test_startup_and_reconnect_use_identical_desired_state_reconciliation():
    first = backend()
    second = backend(dpi=1500, rate=500)
    desired = DesiredHardwareState(1500, (800, 1500, 2000), 500)
    supervisor = HardwareSupervisor(first, G305, lambda _device: second, desired)

    supervisor.reconcile()
    names = [entry[0] for entry in first.method_calls]
    assert names.index("set_polling_rate") < names.index("set_dpi")
    first.set_polling_rate.assert_called_once_with(G305, 500)
    first.set_dpi.assert_called_once_with(G305, 1500)

    assert supervisor.rebind(expected_generation=0)
    second.set_polling_rate.assert_called_once_with(G305, 500)
    second.set_dpi.assert_called_once_with(G305, 1500)
    first.close.assert_called_once()
    assert supervisor.current_backend is second
    assert supervisor.generation == 1


def test_stale_rebind_request_cannot_replace_new_backend_twice():
    first, second, third = backend(), backend(), backend()
    replacements = iter((second, third))
    supervisor = HardwareSupervisor(first, G305, lambda _device: next(replacements))
    assert supervisor.rebind(expected_generation=0)
    assert not supervisor.rebind(expected_generation=0)
    assert supervisor.current_backend is second
    third.close.assert_not_called()


def test_dpi_battery_and_events_all_follow_the_current_backend():
    first, second = backend(dpi=800), backend(dpi=1500)
    supervisor = HardwareSupervisor(first, G305, lambda _device: second)
    cycler = DpiCycler(supervisor, G305, [800, 1500], 800, notifier=MagicMock())

    supervisor.rebind(0)
    assert cycler.cycle()
    assert supervisor.desired.active_dpi == 1500
    second.set_dpi.assert_called_once_with(G305, 1500)
    first.set_dpi.assert_not_called()
    assert supervisor.get_battery_state(G305) == BatteryState(percentage=90)
    second.get_battery_state.assert_called_once_with(G305)

    callback, stop = MagicMock(), MagicMock()
    supervisor.watch_dpi_events(G305, callback, stop)
    second.watch_dpi_events.assert_called_once_with(G305, callback, stop, None)


def test_reconnect_reapplies_last_successful_runtime_dpi():
    first, second = backend(dpi=800), backend(dpi=800)
    supervisor = HardwareSupervisor(
        first, G305, lambda _device: second,
        DesiredHardwareState(active_dpi=800, dpi_stages=(800, 1500)))
    cycler = DpiCycler(supervisor, G305, [800, 1500], 800, notifier=MagicMock())
    assert cycler.cycle()
    supervisor.rebind(0)
    second.set_dpi.assert_called_once_with(G305, 1500)
    assert supervisor.get_dpi(G305) == 1500


def test_shutdown_closes_current_backend_exactly_once():
    target = backend()
    supervisor = HardwareSupervisor(target, G305, lambda _device: backend())
    supervisor.close()
    supervisor.close()
    target.close.assert_called_once()


def test_rebind_updates_device_identity_used_by_existing_consumers():
    configured = MouseDevice("G305", "/dev/input/stable", vendor=0x046d,
                             product=0x4074)
    resolved = MouseDevice("G305", "/dev/input/event9", phys="usb-new",
                           vendor=0x046d, product=0x4074, bustype=3)
    first, second = backend(), backend()
    supervisor = HardwareSupervisor(
        first, configured, lambda device: second,
        device_resolver=lambda _old: resolved, discovery_pending=True)
    supervisor.rebind(0)
    supervisor.get_dpi(configured)
    second.get_dpi.assert_called_with(resolved)
    assert supervisor.device == resolved
    assert not supervisor.discovery_pending


def test_reconnect_promotes_native_after_temporary_generic_fallback():
    """Incomplete hotplug discovery must not make Generic permanently sticky."""
    native_before, native_after = backend(), backend()
    fallback = GenericBackend()
    fallback.close = MagicMock()
    duplicate_fallback = GenericBackend()
    duplicate_fallback.close = MagicMock()
    replacements = iter((fallback, duplicate_fallback, native_after))
    supervisor = HardwareSupervisor(
        native_before, G305, lambda _device: next(replacements))

    assert supervisor.rebind(0)
    assert supervisor.current_backend is fallback
    assert supervisor.discovery_pending
    assert supervisor.generation == 1
    native_before.close.assert_called_once()

    # A settling probe that finds only Generic does not replace the live
    # fallback or advance its generation.
    assert not supervisor.rebind(1)
    assert supervisor.current_backend is fallback
    assert supervisor.generation == 1
    duplicate_fallback.close.assert_called_once()

    assert supervisor.rebind(1)
    assert supervisor.current_backend is native_after
    assert not supervisor.discovery_pending
    assert supervisor.generation == 2
    fallback.close.assert_called_once()
