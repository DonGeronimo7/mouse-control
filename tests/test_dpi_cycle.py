from pathlib import Path
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from evdev import ecodes
from mouse_control.discovery import MouseDevice
from mouse_control.hardware import HardwareBackend, HardwareError
from mouse_control.hardware.generic import GenericBackend
from mouse_control.hardware.capabilities import DpiState
from mouse_control.notifications import DpiEventMonitor, DpiMonitorSupervisor
from mouse_control.remapper import DpiCycler, MouseRemapper
from mouse_control import cli


MOUSE = MouseDevice("Test", "/dev/input/test")


def cycler(stages=(800, 1500, 2000, 2500, 3000), current=800, enabled=True):
    backend = MagicMock(spec=HardwareBackend)
    backend.name = "Test"
    backend.supports_dpi.return_value = True
    backend.get_dpi.return_value = None
    notifier = MagicMock()
    return DpiCycler(backend, MOUSE, list(stages), current, enabled, notifier), backend, notifier


def test_cycles_all_configured_stages_and_wraps():
    target, backend, notifier = cycler()
    observed = []
    for _ in range(5):
        assert target.cycle()
        observed.append(target.current_dpi)
    assert observed == [1500, 2000, 2500, 3000, 800]
    assert [call.args[1] for call in backend.set_dpi.call_args_list] == observed
    assert [call.args[0] for call in notifier.notify_dpi.call_args_list] == observed


def test_failed_set_leaves_state_and_does_not_notify():
    target, backend, notifier = cycler()
    backend.set_dpi.side_effect = HardwareError("disconnected")
    assert not target.cycle()
    assert target.current_dpi == 800
    notifier.notify_dpi.assert_not_called()


def test_notifications_can_be_disabled_without_disabling_cycle():
    target, backend, notifier = cycler(enabled=False)
    assert target.cycle()
    backend.set_dpi.assert_called_once_with(MOUSE, 1500)
    notifier.notify_dpi.assert_not_called()


def test_backend_without_dpi_support_is_nonfatal():
    target, backend, notifier = cycler()
    backend.supports_dpi.return_value = False
    assert not target.cycle()
    assert target.current_dpi == 800
    backend.set_dpi.assert_not_called()
    notifier.notify_dpi.assert_not_called()


def test_remapper_consumes_dpi_action_on_press_only_and_key_mapping_still_emits():
    target, _, _ = cycler()
    remapper = object.__new__(MouseRemapper)
    remapper.mappings = {
        ecodes.BTN_TASK: __import__('mouse_control.remapper', fromlist=['parse_action']).parse_action('dpi-cycle'),
        ecodes.BTN_EXTRA: __import__('mouse_control.remapper', fromlist=['parse_action']).parse_action('key:KEY_LEFTMETA'),
    }
    remapper.dpi_cycler = target
    remapper.ui = MagicMock()
    remapper._handle(ecodes.EV_KEY, ecodes.BTN_TASK, 1)
    remapper._handle(ecodes.EV_KEY, ecodes.BTN_TASK, 0)
    assert target.current_dpi == 1500
    remapper._handle(ecodes.EV_KEY, ecodes.BTN_EXTRA, 1)
    remapper.ui.write.assert_called_once_with(ecodes.EV_KEY, ecodes.KEY_LEFTMETA, 1)


def run_config(mappings, backend):
    config = {
        'device': {'event_path': MOUSE.path},
        'dpi': {'active': 800, 'stages': [800, 1500]},
        'notifications': {'dpi_changes': True},
        'remap': mappings,
    }
    monitor_instance = MagicMock()
    with patch.object(cli, 'load_config', return_value=config), \
         patch.object(cli, 'get_mouse_devices', return_value=[MOUSE]), \
         patch.object(cli, 'get_backend', return_value=backend), \
         patch.object(cli, 'DpiMonitorSupervisor', return_value=monitor_instance) as factory, \
         patch.object(cli, 'MouseRemapper') as remapper:
        assert cli.run_from_config() == 0
    return factory, monitor_instance, remapper


def runtime_backend():
    backend = MagicMock(spec=HardwareBackend)
    backend.name = 'Test'
    backend.supports_dpi.return_value = True
    backend.supports_dpi_stages.return_value = False
    backend.supports_polling_rate.return_value = False
    return backend


def test_runtime_without_dpi_cycle_creates_monitor_only():
    factory, monitor, remapper = run_config({'BTN_LEFT': 'passthrough'}, runtime_backend())
    factory.assert_called_once()
    monitor.start.assert_called_once()
    monitor.stop.assert_called_once()
    assert remapper.call_args.args[3] is None


def test_runtime_with_dpi_cycle_creates_cycler_and_monitor():
    factory, monitor, remapper = run_config({'BTN_TASK': 'dpi-cycle'}, runtime_backend())
    factory.assert_called_once()
    monitor.start.assert_called_once()
    monitor.stop.assert_called_once()
    target = remapper.call_args.args[3]
    assert isinstance(target, DpiCycler)
    assert target.notifier is monitor
    assert factory.call_args.args[5] is remapper.call_args.args[2]


def test_g305_runtime_wires_physical_cycler_without_changing_button_mappings():
    g305 = MouseDevice("G305", MOUSE.path, vendor=0x046d, product=0x4074, bustype=3)
    config = {
        'device': {'event_path': g305.path},
        'dpi': {'active': 1000, 'stages': [1000, 1500, 2000, 2500, 3000]},
        'notifications': {'dpi_changes': False},
        'remap': {'BTN_EXTRA': 'key:KEY_LEFTMETA',
                  'BTN_SIDE': 'chord:KEY_LEFTCTRL+KEY_LEFTSHIFT+KEY_S'},
    }
    backend = runtime_backend()
    with patch.object(cli, 'load_config', return_value=config), \
         patch.object(cli, 'get_mouse_devices', return_value=[g305]), \
         patch.object(cli, 'get_backend', return_value=backend), \
         patch.object(cli, 'DpiMonitorSupervisor') as factory, \
         patch.object(cli, 'MouseRemapper') as remapper:
        assert cli.run_from_config() == 0
    target = remapper.call_args.args[3]
    assert isinstance(target, DpiCycler)
    assert factory.call_args.kwargs['dpi_cycler'] is target
    assert factory.call_args.kwargs['notifications_enabled'] is False
    assert remapper.call_args.args[1] == config['remap']


def test_g305_late_receiver_still_wires_physical_cycler():
    config = {
        'device': {'event_path': MOUSE.path, 'vendor': 0x046d,
                   'product': 0x4074, 'bustype': 3},
        'dpi': {'active': 1000, 'stages': [1000, 1500]},
        'notifications': {'dpi_changes': False},
        'remap': {},
    }
    backend = runtime_backend()
    with patch.object(cli, 'load_config', return_value=config), \
         patch.object(cli, 'get_mouse_devices', return_value=[]), \
         patch.object(cli, 'get_backend', return_value=backend), \
         patch.object(cli, 'DpiMonitorSupervisor') as factory, \
         patch.object(cli, 'MouseRemapper') as remapper:
        assert cli.run_from_config() == 0
    assert factory.call_args.kwargs['dpi_cycler'] is remapper.call_args.args[3]


def test_cycler_and_event_monitor_suppress_same_value_hardware_echo():
    target, backend, _ = cycler(stages=(800, 1500))
    notifier = MagicMock()
    monitor = DpiEventMonitor(backend, MOUSE, [800, 1500], 800, notifier)
    target.notifier = monitor
    backend.set_dpi.return_value = DpiState(1500, confirmed=True)
    assert target.cycle()
    monitor.handle_state(DpiState(1500, active_stage=1, confirmed=True))
    notifier.notify_dpi.assert_called_once_with(1500)


def test_cycle_rejects_mismatched_readback_without_advancing_or_notifying():
    target, backend, notifier = cycler(stages=(800, 1400, 2000))
    backend.get_dpi.side_effect = [1450, 1400]
    assert not target.cycle()
    assert target.current_dpi == 800
    notifier.notify_dpi.assert_not_called()
    assert target.cycle()
    assert [call.args[1] for call in backend.set_dpi.call_args_list] == [1400, 1400]
    notifier.notify_dpi.assert_called_once_with(1400)


def test_rapid_deliberate_presses_notify_even_when_readback_repeats():
    target, backend, _ = cycler(stages=(800,), current=800)
    backend.get_dpi.return_value = 800
    notifier = MagicMock()
    monitor = DpiEventMonitor(backend, MOUSE, [800], 800, notifier)
    target.notifier = monitor
    assert target.cycle()
    assert target.cycle()
    assert [call.args[0] for call in notifier.notify_dpi.call_args_list] == [800, 800]


def test_g305_physical_events_use_configured_cycler_and_ignore_write_echoes():
    stages = [1000, 1500, 2000, 2500, 3000]
    target, backend, _ = cycler(stages=stages, current=1000)
    notifier = MagicMock()
    monitor = DpiEventMonitor(backend, MOUSE, stages, 1000, notifier,
                              dpi_cycler=target)
    target.notifier = monitor
    live = 1000
    def set_dpi(_device, requested):
        nonlocal live
        live = requested
        return DpiState(live, confirmed=True)
    backend.set_dpi.side_effect = set_dpi
    backend.get_dpi.side_effect = lambda _device: live
    for onboard_stage, onboard_dpi, expected in zip(
            [1, 2, 3, 4, 0], [1500, 2000, 2500, 3000, 800],
            [1500, 2000, 2500, 3000, 1000]):
        monitor.handle_state(DpiState(onboard_dpi, active_stage=onboard_stage,
                                      confirmed=True))
        # A write echo and a duplicate original transition must not cycle again.
        monitor.handle_state(DpiState(expected, active_stage=onboard_stage,
                                      confirmed=True))
        monitor.handle_state(DpiState(onboard_dpi, active_stage=onboard_stage,
                                      confirmed=True))
        assert target.current_dpi == expected
    assert [call.args[1] for call in backend.set_dpi.call_args_list] == stages[1:] + stages[:1]
    assert [call.args[0] for call in notifier.notify_dpi.call_args_list] == stages[1:] + stages[:1]


def test_confirmed_external_dpi_sets_cycler_cursor_and_off_list_starts_first():
    target, backend, _ = cycler(stages=(1000, 1500, 2000), current=1000)
    notifier = MagicMock()
    monitor = DpiEventMonitor(backend, MOUSE, target.stages, 1000, notifier,
                              dpi_cycler=target)
    target.notifier = monitor
    monitor.handle_state(DpiState(2000, confirmed=True))
    backend.get_dpi.return_value = 1000
    monitor.handle_state(DpiState(800, active_stage=0, confirmed=True))
    backend.set_dpi.assert_called_once_with(MOUSE, 1000)
    monitor.handle_state(DpiState(1750, confirmed=True))
    backend.get_dpi.return_value = 1000
    monitor.handle_state(DpiState(1500, active_stage=1, confirmed=True))
    assert backend.set_dpi.call_args_list[-1].args == (MOUSE, 1000)


def test_runtime_with_dpi_cycle_tolerates_backend_without_monitoring():
    config = {
        'device': {'event_path': MOUSE.path},
        'dpi': {'active': 800, 'stages': [800, 1500]},
        'remap': {'BTN_TASK': 'dpi-cycle'},
    }
    backend = runtime_backend()
    with patch.object(cli, 'load_config', return_value=config), \
         patch.object(cli, 'get_mouse_devices', return_value=[MOUSE]), \
         patch.object(cli, 'get_backend', return_value=backend), \
         patch.object(cli, 'DpiMonitorSupervisor') as factory, \
         patch.object(cli, 'MouseRemapper') as remapper:
        assert cli.run_from_config() == 0
    factory.assert_called_once()
    assert isinstance(remapper.call_args.args[3], DpiCycler)


def test_normal_runtime_wires_native_event_to_desktop_notification(caplog):
    """Exercise the production run path, rather than just monitor methods."""
    caplog.set_level('INFO')
    config = {
        'device': {'event_path': MOUSE.path},
        'dpi': {'active': 800, 'stages': [800, 1500]},
        'notifications': {'dpi_changes': True},
        'remap': {'BTN_LEFT': 'passthrough'},
    }
    backend = runtime_backend()
    backend.name = 'Logitech HID++ 2'
    backend.supports_dpi_events.return_value = True
    notifier = MagicMock()

    emitted = threading.Event()

    def emit_event(_device, callback, shutdown, ready):
        ready()
        callback(DpiState(1500, confirmed=True, active_stage=1))
        emitted.set()
        shutdown.wait(10)

    def run_remapper():
        assert emitted.wait(2)

    backend.watch_dpi_events.side_effect = emit_event
    with patch.object(cli, 'load_config', return_value=config), \
         patch.object(cli, 'get_mouse_devices', return_value=[MOUSE]), \
         patch.object(cli, 'get_backend', return_value=backend), \
         patch('mouse_control.notifications.FreedesktopNotifier', return_value=notifier), \
         patch.object(cli, 'MouseRemapper') as remapper:
        remapper.return_value.run.side_effect = run_remapper
        assert cli.main(['run']) == 0

    notifier.notify_dpi.assert_called_once_with(1500)
    backend.watch_dpi_events.assert_called_once()
    assert 'Selected hardware backend: Logitech HID++ 2' in caplog.text
    assert 'Starting DPI notification monitor' in caplog.text


def test_runtime_survives_unavailable_backend_at_startup_and_notifies_after_bind():
    config = {
        'device': {'event_path': MOUSE.path},
        'dpi': {'active': 800, 'stages': [800, 1500]},
        'notifications': {'dpi_changes': True},
        'remap': {'BTN_LEFT': 'passthrough'},
    }
    backend = runtime_backend()
    backend.supports_dpi_events.return_value = True
    notified = threading.Event()
    notifier = MagicMock()
    notifier.notify_dpi.side_effect = lambda _dpi: notified.set()
    backend.watch_dpi_events.side_effect = lambda _device, callback, shutdown, ready: (
        ready(),
        callback(DpiState(1500, confirmed=True, active_stage=1)),
        shutdown.wait(10),
    )

    def supervisor(*args):
        return DpiMonitorSupervisor(*args, notifier=notifier, retry_interval=0.01)

    with patch.object(cli, 'load_config', return_value=config), \
         patch.object(cli, 'get_mouse_devices', return_value=[MOUSE]), \
         patch.object(cli, 'get_backend', side_effect=[GenericBackend(), backend]) as select, \
         patch.object(cli, 'DpiMonitorSupervisor', side_effect=supervisor), \
         patch.object(cli, 'MouseRemapper') as remapper:
        remapper.return_value.run.side_effect = lambda: notified.wait(2) or pytest.fail(
            "runtime stopped waiting before notification monitoring recovered")
        assert cli.run_from_config() == 0

    assert remapper.return_value.run.call_count == 1
    assert select.call_count == 2
    backend.watch_dpi_events.assert_called_once()
    notifier.notify_dpi.assert_called_once_with(1500)
