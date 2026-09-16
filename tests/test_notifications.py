"""DPI notification tests require neither hardware nor a desktop session."""

from pathlib import Path
import asyncio
import sys
import threading
import time
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mouse_control.discovery import MouseDevice
from mouse_control.hardware.generic import GenericBackend
from mouse_control.hardware import HardwareSupervisor
from mouse_control.hardware.capabilities import DpiState
from mouse_control.notifications import (DpiEventMonitor, DpiMonitor,
                                         DpiMonitorSupervisor, FreedesktopNotifier,
                                         create_dpi_monitor)


MOUSE = MouseDevice("Test", "/dev/input/test", vendor=1, product=2)


def backend_with_dpi(*values):
    backend = Mock()
    backend.supports_dpi_events.return_value = False
    backend.supports_dpi_monitoring.return_value = True
    backend.get_dpi.side_effect = values
    return backend


def test_notification_emitted_only_when_dpi_changes():
    backend = backend_with_dpi(800, 800, 1500)
    notifier = Mock()
    monitor = DpiMonitor(backend, MOUSE, notifier)
    monitor.poll_once()
    monitor.poll_once()
    notifier.notify_dpi.assert_not_called()
    monitor.poll_once()
    notifier.notify_dpi.assert_called_once_with(1500)


def test_five_sequential_dpi_states_submit_five_independent_notifications():
    notifier = FreedesktopNotifier()
    calls = []

    async def connect():
        return Mock()

    async def notify(bus, dpi):
        calls.append(dpi)
        return len(calls) * 10

    notifier._connect = connect
    notifier._notify = notify
    values = [800, 1500, 2000, 2500, 3000]
    for dpi in values:
        notifier.notify_dpi(dpi)
    notifier.wait_idle()
    notifier.close()
    assert calls == values


def test_each_dpi_notify_call_has_zero_replaces_id_and_osd_options():
    from dbus_next.constants import MessageType

    notifier = FreedesktopNotifier()
    messages = []

    async def call(message):
        messages.append(message)
        return Mock(message_type=MessageType.METHOD_RETURN, body=[len(messages)])

    bus = Mock()
    bus.call = call
    values = [800, 1500, 2000, 2500, 3000]
    for dpi in values:
        asyncio.run(notifier._notify(bus, dpi))

    assert len(messages) == 5
    assert [message.member for message in messages] == ["Notify"] * 5
    assert [message.body[1] for message in messages] == [0] * 5
    assert [message.body[4] for message in messages] == [f"{dpi} DPI" for dpi in values]
    assert all(message.body[6]["transient"].value is True for message in messages)
    assert all(message.body[7] == 1500 for message in messages)


def test_rapid_dpi_sequence_is_not_debounced_or_coalesced():
    notifier = FreedesktopNotifier()
    calls = []

    async def connect():
        return Mock()

    async def notify(bus, dpi):
        calls.append(dpi)
        return len(calls)

    notifier._connect = connect
    notifier._notify = notify
    values = [800, 1500, 2000, 2500, 3000]
    for dpi in values:
        notifier.notify_dpi(dpi)
    notifier.wait_idle()
    notifier.close()
    assert calls == values


def test_notification_uses_transient_silent_hints_and_short_timeout():
    from dbus_next.constants import MessageType

    notifier = FreedesktopNotifier()
    bus = Mock()
    reply = Mock(message_type=MessageType.METHOD_RETURN, body=[11])
    bus.call = Mock(return_value=reply)

    async def call(message):
        bus.message = message
        return reply

    bus.call = call
    assert asyncio.run(notifier._notify(bus, 1500)) == 11
    body = bus.message.body
    assert body[1] == 0
    assert body[4] == "1500 DPI"
    assert body[6]["urgency"].value == 1
    assert body[6]["transient"].value is True
    assert body[6]["suppress-sound"].value is True
    assert body[7] == 1500

    # An old lifecycle ID must never turn a DPI OSD into a replacement.
    notifier._notification_id = 11
    assert asyncio.run(notifier._notify(bus, 2000)) == 11
    assert bus.message.body[1] == 0


def test_one_dbus_failure_does_not_disable_future_notifications(caplog):
    notifier = FreedesktopNotifier()
    attempts = 0

    async def connect():
        return Mock()

    async def notify(bus, dpi):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError('bus reset')
        return 31

    notifier._connect = connect
    notifier._notify = notify
    notifier.notify_dpi(1500)
    notifier.notify_dpi(2000)
    notifier.wait_idle()
    notifier.close()
    assert attempts == 2
    assert 'DPI notification failed' in caplog.text


def test_notification_body_displays_dpi():
    assert FreedesktopNotifier._body(1500) == "1500 DPI"
    assert FreedesktopNotifier._body((1500, 1500)) == "1500 DPI"
    assert FreedesktopNotifier._body((1200, 800)) == "1200 × 800 DPI"


def test_disabled_and_unsupported_backends_do_not_monitor():
    capable = backend_with_dpi(800)
    assert create_dpi_monitor(capable, MOUSE, enabled=False) is None
    capable.supports_dpi_monitoring.assert_not_called()
    assert create_dpi_monitor(GenericBackend(), MOUSE) is None


def test_monitor_capability_failure_is_nonfatal(caplog):
    backend = Mock()
    backend.supports_dpi_monitoring.side_effect = RuntimeError("service unavailable")
    assert create_dpi_monitor(backend, MOUSE) is None
    assert "service unavailable" in caplog.text


def test_notification_failure_does_not_stop_monitor_and_warning_is_deduplicated(caplog):
    backend = backend_with_dpi(800, 1500, 2000, 2500)
    notifier = Mock()
    notifier.notify_dpi.side_effect = [RuntimeError("no notification server"),
                                       RuntimeError("no notification server"), None]
    monitor = DpiMonitor(backend, MOUSE, notifier)
    for _ in range(4):
        monitor.poll_once()
    assert notifier.notify_dpi.call_count == 3
    assert caplog.text.count("Desktop DPI notification failed") == 1


def test_backend_read_failure_does_not_stop_monitor(caplog):
    backend = backend_with_dpi(800, RuntimeError("disconnected"), 1500)
    notifier = Mock()
    monitor = DpiMonitor(backend, MOUSE, notifier)
    monitor.poll_once()
    monitor.poll_once()
    monitor.poll_once()
    notifier.notify_dpi.assert_called_once_with(1500)
    assert "disconnected" in caplog.text


def test_event_monitor_uses_confirmed_hardware_dpi_not_configured_stage_array():
    notifier = Mock()
    monitor = DpiEventMonitor(Mock(), MOUSE, [800, 1500, 2000, 2500, 3000],
                              800, notifier)
    monitor.handle_state(DpiState(1750, 1750, active_stage=4, confirmed=True))
    notifier.notify_dpi.assert_called_once_with(1750)


def test_event_monitor_notifies_first_confirmed_startup_value():
    notifier = Mock()
    monitor = DpiEventMonitor(Mock(), MOUSE, [800, 1500, 2000], 800, notifier)

    monitor.handle_state(DpiState(800, confirmed=True))

    notifier.notify_dpi.assert_called_once_with(800)


def test_event_monitor_suppresses_only_duplicate_dpi_and_unconfirmed_state():
    notifier = Mock()
    monitor = DpiEventMonitor(Mock(), MOUSE, [800, 1500, 2000], 800, notifier)
    for state in (DpiState(800, confirmed=True), DpiState(1500),
                  DpiState(1500, confirmed=True), DpiState(1500, confirmed=True),
                  DpiState(2000, confirmed=True)):
        monitor.handle_state(state)
    assert [call.args[0] for call in notifier.notify_dpi.call_args_list] == [800, 1500, 2000]


def test_event_monitor_submits_each_ordered_confirmed_transition():
    notifier = Mock()
    monitor = DpiEventMonitor(Mock(), MOUSE, [800, 1500, 2000, 2500, 3000], 800, notifier)

    for dpi in (800, 1500, 2000, 2500, 3000):
        monitor.handle_state(DpiState(dpi, confirmed=True))

    assert [call.args[0] for call in notifier.notify_dpi.call_args_list] == [
        800, 1500, 2000, 2500, 3000,
    ]


def test_application_cycle_and_hardware_echo_share_duplicate_state():
    notifier = Mock()
    monitor = DpiEventMonitor(Mock(), MOUSE, [800, 1500], 800, notifier)
    monitor.notify_dpi(1500)
    monitor.handle_state(DpiState(1500, confirmed=True))
    notifier.notify_dpi.assert_called_once_with(1500)


def test_hardware_event_before_application_confirmation_is_also_deduplicated():
    notifier = Mock()
    monitor = DpiEventMonitor(Mock(), MOUSE, [800, 1500], 800, notifier)
    monitor.handle_state(DpiState(1500, confirmed=True))
    monitor.notify_dpi(1500)
    notifier.notify_dpi.assert_called_once_with(1500)


def test_event_monitor_continues_after_notification_failure():
    backend = Mock()
    notifier = Mock()
    notifier.notify_dpi.side_effect = [RuntimeError('relay failed'), None]
    monitor = DpiEventMonitor(backend, MOUSE, [800, 1500, 2000], 800, notifier)
    monitor.handle_state(DpiState(1500, confirmed=True))
    monitor.handle_state(DpiState(2000, confirmed=True))
    assert notifier.notify_dpi.call_count == 2
    assert monitor._last_notified_dpi == 2000


def test_event_monitor_is_selected_and_disabled_monitor_never_probes():
    backend = Mock()
    backend.supports_dpi_events.return_value = True
    monitor = create_dpi_monitor(backend, MOUSE, stages=[800, 1500], active_dpi=800)
    assert isinstance(monitor, DpiEventMonitor)
    assert create_dpi_monitor(backend, MOUSE, enabled=False,
                              stages=[800, 1500], active_dpi=800) is None
    assert backend.supports_dpi_events.call_count == 1


class ScriptedShutdown:
    """Drive retry iterations without sleeping or racing worker threads."""

    def __init__(self, stop_after=None):
        self.stopped = False
        self.waits = []
        self.stop_after = stop_after

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, delay):
        self.waits.append(delay)
        if self.stop_after is not None and len(self.waits) >= self.stop_after:
            self.set()
        return self.stopped


def event_backend():
    backend = Mock(name="event backend")
    backend.name = "HID++"
    backend.supports_dpi_events.return_value = True
    return backend


def test_supervisor_retries_failed_startup_probe_then_notifies():
    unavailable = Mock(name="unavailable backend")
    unavailable.supports_dpi_events.side_effect = OSError("receiver settling")
    ready = event_backend()
    notifier = Mock()
    shutdown = ScriptedShutdown()

    def deliver(_device, callback, stop, ready):
        ready()
        callback(DpiState(1500, confirmed=True, active_stage=1))
        stop.set()

    ready.watch_dpi_events.side_effect = deliver
    factory = Mock(return_value=ready)
    supervisor = DpiMonitorSupervisor(unavailable, MOUSE, factory, [800, 1500],
                                      800, shutdown, notifier, retry_interval=2.0)
    supervisor._run()

    assert unavailable.supports_dpi_events.call_count == 1
    assert factory.call_count == 1
    assert shutdown.waits == [2.0]
    ready.watch_dpi_events.assert_called_once()
    notifier.notify_dpi.assert_called_once_with(1500)


def test_supervisor_rebinds_after_watcher_disconnect_and_notifies_again():
    first = event_backend()
    second = event_backend()
    notifier = Mock()
    shutdown = ScriptedShutdown()

    def disconnect(_device, callback, _stop, ready):
        ready()
        callback(DpiState(1500, confirmed=True, active_stage=1))
        raise OSError("disconnected")

    def reconnect(_device, callback, stop, ready):
        ready()
        callback(DpiState(2000, confirmed=True, active_stage=2))
        stop.set()

    first.watch_dpi_events.side_effect = disconnect
    second.watch_dpi_events.side_effect = reconnect
    factory = Mock(return_value=second)
    supervisor = DpiMonitorSupervisor(first, MOUSE, factory, [800, 1500, 2000],
                                      800, shutdown, notifier, retry_interval=3.0)
    supervisor._run()

    assert shutdown.waits == [3.0]
    first.close.assert_called_once()
    factory.assert_called_once_with(MOUSE)
    second.watch_dpi_events.assert_called_once()
    assert [call.args[0] for call in notifier.notify_dpi.call_args_list] == [1500, 2000]


def test_event_monitor_recovers_when_native_arrives_after_generic_fallback():
    """A provisional Generic bind must be retried until Native HID returns."""
    first, promoted = event_backend(), event_backend()
    fallback = GenericBackend()
    fallback.close = Mock()
    notifier = Mock()
    shutdown = ScriptedShutdown()
    replacements = iter((fallback, GenericBackend(), promoted))

    def disconnect(_device, _callback, _stop, ready):
        ready()
        raise OSError("receiver removed")

    def deliver(_device, callback, stop, ready):
        ready()
        callback(DpiState(1500, confirmed=True, active_stage=1))
        stop.set()

    first.watch_dpi_events.side_effect = disconnect
    promoted.watch_dpi_events.side_effect = deliver
    hardware = HardwareSupervisor(first, MOUSE, lambda _device: next(replacements))
    monitor = DpiMonitorSupervisor(hardware, MOUSE, lambda _device: hardware,
                                   [800, 1500], 800, shutdown, notifier,
                                   retry_interval=2.0)
    monitor._run()

    assert hardware.current_backend is promoted
    assert hardware.generation == 2
    fallback.close.assert_called_once()
    promoted.watch_dpi_events.assert_called_once()
    notifier.notify_dpi.assert_called_once_with(1500)


def test_supervisor_rebind_allows_same_first_confirmed_value_after_disconnect():
    first = event_backend()
    second = event_backend()
    notifier = Mock()
    shutdown = ScriptedShutdown()

    def disconnect(_device, callback, _stop, ready):
        ready()
        callback(DpiState(800, confirmed=True, active_stage=0))
        raise OSError("disconnected")

    def reconnect(_device, callback, stop, ready):
        ready()
        callback(DpiState(800, confirmed=True, active_stage=0))
        stop.set()

    first.watch_dpi_events.side_effect = disconnect
    second.watch_dpi_events.side_effect = reconnect
    supervisor = DpiMonitorSupervisor(first, MOUSE, Mock(return_value=second), [800],
                                      800, shutdown, notifier, retry_interval=3.0)
    supervisor._run()

    assert [call.args[0] for call in notifier.notify_dpi.call_args_list] == [800, 800]


def test_late_event_backend_notifies_its_first_configured_value():
    unavailable = Mock(name="unavailable backend")
    unavailable.supports_dpi_events.side_effect = OSError("mouse absent")
    ready = event_backend()
    notifier = Mock()
    shutdown = ScriptedShutdown()

    def deliver(_device, callback, stop, ready):
        ready()
        callback(DpiState(800, confirmed=True, active_stage=0))
        stop.set()

    ready.watch_dpi_events.side_effect = deliver
    supervisor = DpiMonitorSupervisor(unavailable, MOUSE, Mock(return_value=ready), [800],
                                      800, shutdown, notifier, retry_interval=2.0)
    supervisor._run()

    notifier.notify_dpi.assert_called_once_with(800)


def test_first_event_is_submitted_without_notifier_readiness_gating():
    """The notifier owns its queue; watcher delivery has no second FIFO."""
    class DelayedNotifier:
        def __init__(self):
            self.ready = False
            self.listener = None
            self.submissions = []

        def start(self):
            pass

        def is_ready(self):
            return self.ready

        def add_ready_listener(self, callback):
            self.listener = callback

        def notify_dpi(self, dpi):
            self.submissions.append(dpi)

    backend = event_backend()
    notifier = DelayedNotifier()
    shutdown = threading.Event()

    def deliver(_device, callback, _stop, ready):
        ready()
        callback(DpiState(800, confirmed=True, active_stage=0))
        callback(DpiState(1500, confirmed=True, active_stage=1))
        assert notifier.submissions == [800, 1500]
        notifier.ready = True
        shutdown.set()

    backend.watch_dpi_events.side_effect = deliver
    supervisor = DpiMonitorSupervisor(backend, MOUSE, Mock(), [800, 1500], 800,
                                      shutdown, notifier, retry_interval=0.01)
    supervisor._run()

    assert notifier.submissions == [800, 1500]


def test_supervisor_repeated_unavailable_has_bounded_wait_and_one_warning(caplog):
    backend = Mock()
    backend.supports_dpi_events.side_effect = OSError("not ready")
    shutdown = ScriptedShutdown(stop_after=4)
    factory = Mock(return_value=backend)
    supervisor = DpiMonitorSupervisor(backend, MOUSE, factory, [800], 800,
                                      shutdown, Mock(), retry_interval=5.0)
    supervisor._run()

    assert backend.supports_dpi_events.call_count == 4
    assert shutdown.waits == [5.0] * 4
    assert factory.call_count == 3
    assert caplog.text.count("DPI monitoring unavailable; retrying") == 1


def test_supervisor_shutdown_interrupts_long_retry_wait():
    probed = threading.Event()
    shutdown = threading.Event()
    backend = Mock()

    def unavailable(_device):
        probed.set()
        raise OSError("not ready")

    backend.supports_dpi_events.side_effect = unavailable
    factory = Mock()
    supervisor = DpiMonitorSupervisor(backend, MOUSE, factory, [800], 800,
                                      shutdown, Mock(), retry_interval=100)
    supervisor.start()
    assert probed.wait(2)
    supervisor.stop()

    assert supervisor._thread is not None
    assert not supervisor._thread.is_alive()
    factory.assert_not_called()


def test_failed_watcher_start_is_closed_then_replaced_by_fresh_backend():
    failed = event_backend()
    recovered = event_backend()
    notifier = Mock()
    shutdown = ScriptedShutdown()

    failed.watch_dpi_events.side_effect = OSError("stale HID session")

    def deliver(_device, callback, stop, ready):
        ready()
        callback(DpiState(1500, confirmed=True))
        stop.set()

    recovered.watch_dpi_events.side_effect = deliver
    factory = Mock(return_value=recovered)
    supervisor = DpiMonitorSupervisor(failed, MOUSE, factory, [800, 1500], 800,
                                      shutdown, notifier, retry_interval=1.0)
    supervisor._run()

    failed.close.assert_called_once()
    factory.assert_called_once_with(MOUSE)
    notifier.notify_dpi.assert_called_once_with(1500)
