"""Setup transaction and background-service coordination tests."""

from pathlib import Path
import sys
from unittest.mock import Mock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mouse_control import cli
from mouse_control.discovery import MouseDevice
from mouse_control.wizard import ButtonCaptureError


MOUSE = MouseDevice("Test Mouse", "/dev/input/test", vendor=1, product=2)


@pytest.fixture(autouse=True)
def wizard_answers(request):
    service = "" if request.node.name in ("test_existing_autostart_flow_starts_once_without_duplicate_restart", "test_inactive_service_autostart_uses_existing_install_flow_once") else "n"
    with patch('builtins.input', side_effect=['e', '', '', service, '']):
        yield


def setup_patches(*, active=False, selected=MOUSE, capture=None, autostart=False):
    backend = Mock()
    backend.supports_dpi.return_value = False
    backend.supports_polling_rate.return_value = False
    backend.supports_polling_rate_writes.return_value = False
    return (
        backend,
        patch.object(cli, "is_service_active", return_value=active),
        patch.object(cli, "get_mouse_devices", return_value=[MOUSE]),
        patch.object(cli, "select_mouse_device", return_value=selected),
        patch.object(cli, "get_backend", return_value=backend),
        patch.object(cli, "map_mouse_buttons", return_value={} if capture is None else capture),
        patch.object(cli, "_ask_enable_service", return_value=autostart),
        patch.object(cli, "_apply_hardware"),
        patch.object(cli, "save_config", return_value=Path("/tmp/config.toml")),
        patch.object(cli, "stop_service"),
        patch.object(cli, "restart_service"),
        patch.object(cli, "install_service"),
    )


def test_running_service_stops_before_capture_and_restarts_after_success():
    events = []
    parts = setup_patches(active=True)
    backend, *contexts = parts
    with contexts[0], contexts[1], contexts[2], contexts[3], \
         contexts[4] as capture, contexts[5], contexts[6], contexts[7], \
         contexts[8] as stop, contexts[9] as restart, contexts[10] as install:
        stop.side_effect = lambda: events.append("stop")
        capture.side_effect = lambda path: events.append("capture") or {}
        assert cli.run_setup_wizard() == 0
    assert events == ["stop", "capture"]
    restart.assert_called_once()
    install.assert_not_called()


def test_inactive_service_declined_autostart_remains_inactive():
    backend, *contexts = setup_patches(active=False)
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], \
         contexts[5], contexts[6], contexts[7], contexts[8] as stop, \
         contexts[9] as restart, contexts[10] as install:
        assert cli.run_setup_wizard() == 0
    stop.assert_not_called()
    restart.assert_not_called()
    install.assert_not_called()


def test_existing_autostart_flow_starts_once_without_duplicate_restart():
    backend, *contexts = setup_patches(active=True, autostart=True)
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], \
         contexts[5], contexts[6], contexts[7], contexts[8] as stop, \
         contexts[9] as restart, contexts[10] as install:
        assert cli.run_setup_wizard() == 0
    stop.assert_called_once()
    install.assert_called_once()
    restart.assert_not_called()


def test_inactive_service_autostart_uses_existing_install_flow_once():
    backend, *contexts = setup_patches(active=False, autostart=True)
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], \
         contexts[5], contexts[6], contexts[7], contexts[8] as stop, \
         contexts[9] as restart, contexts[10] as install:
        assert cli.run_setup_wizard() == 0
    stop.assert_not_called()
    install.assert_called_once()
    restart.assert_not_called()


def test_capture_failure_preserves_config_and_restores_running_service(capsys):
    backend, *contexts = setup_patches(active=True)
    with contexts[0], contexts[1], contexts[2], contexts[3], \
         contexts[4] as capture, contexts[5], contexts[6], \
         contexts[7] as save, contexts[8], contexts[9] as restart, contexts[10]:
        capture.side_effect = ButtonCaptureError("busy")
        assert cli.run_setup_wizard() == 1
    save.assert_not_called()
    restart.assert_called_once()
    assert "Setup failed" in capsys.readouterr().err


def test_prompt_cancellation_after_capture_does_not_commit_config():
    backend, *contexts = setup_patches(active=True)
    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], \
         contexts[5] as ask, contexts[6], contexts[7] as save, contexts[8], \
         contexts[9] as restart, contexts[10]:
        with patch("builtins.input", side_effect=["", "", "", KeyboardInterrupt]):
            assert cli.run_setup_wizard() == 1
    save.assert_not_called()
    restart.assert_called_once()


def test_setup_cancellation_preserves_config_and_restores_running_service():
    backend, *contexts = setup_patches(active=True, selected=None)
    with contexts[0], contexts[1], contexts[2], contexts[3], \
         contexts[4] as capture, contexts[5], contexts[6], \
         contexts[7] as save, contexts[8], contexts[9] as restart, contexts[10]:
        assert cli.run_setup_wizard() == 0
    capture.assert_not_called()
    save.assert_not_called()
    restart.assert_called_once()
