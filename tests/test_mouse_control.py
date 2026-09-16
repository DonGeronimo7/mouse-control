import sys
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from evdev import ecodes
from mouse_control.config import DEFAULT_DPI, DEFAULT_DPI_STAGES, generate_config
from mouse_control.discovery import MouseDevice, has_mouse_capabilities
from mouse_control.remapper import parse_action
from mouse_control.wizard import get_button_name
from mouse_control.permissions import UINPUT_PATH, permission_report
from mouse_control.service import build_service_text


def test_mouse_capabilities():
    device = MagicMock()
    device.capabilities.return_value = {ecodes.EV_KEY: [], ecodes.EV_REL: [ecodes.REL_X]}
    assert has_mouse_capabilities(device)


def test_get_button_name():
    assert get_button_name(ecodes.BTN_LEFT) == "BTN_LEFT"


def test_parse_actions():
    assert parse_action("passthrough").kind == "passthrough"
    assert parse_action("disable").kind == "disable"
    assert parse_action("dpi-cycle").kind == "dpi-cycle"
    assert parse_action("mouse:BTN_MIDDLE").code == ecodes.BTN_MIDDLE
    assert parse_action("key:KEY_F13").code == ecodes.KEY_F13


def test_config_contains_executable_actions():
    mouse = MouseDevice("Test Mouse", "/dev/input/test", phys="usb-test",
                        vendor=0x046D, product=0xC332, bustype=3)
    content = generate_config(
        mouse,
        {"BTN_LEFT": "passthrough", "BTN_SIDE": "key:KEY_LEFTCTRL"},
        active_dpi=1600,
    )
    assert "phys = 'usb-test'" in content
    assert "bustype = 3" in content
    assert "BTN_SIDE" in content
    assert "key:KEY_LEFTCTRL" in content
    assert "active = 1600" in content
    assert "[notifications]" in content
    assert "dpi_changes = true" in content
    assert "stages = [800, 1500, 2000, 2500, 3000]" in generate_config(
        mouse, {}, dpi_stages=DEFAULT_DPI_STAGES, active_dpi=DEFAULT_DPI
    )


def test_explicit_empty_dpi_stage_list_is_not_replaced_by_defaults():
    mouse = MouseDevice("Test", "/dev/input/test")
    assert "stages = []" in generate_config(mouse, {}, dpi_stages=[])


def test_service_quotes_the_resolved_executable_path():
    text = build_service_text('/opt/Mouse Control/bin/mouse-control')
    assert 'ExecStart="/opt/Mouse Control/bin/mouse-control" run' in text
    assert 'WantedBy=default.target' in text


def test_permission_report_requires_mouse_and_uinput_access(capsys):
    with patch('mouse_control.permissions.UINPUT_PATH') as uinput, \
         patch('mouse_control.permissions.get_mouse_devices', return_value=[]):
        uinput.exists.return_value = False
        assert permission_report() == 1
    assert '/dev/uinput is absent' in capsys.readouterr().out


def test_udev_rule_is_not_world_writable_or_keyboard_permissive():
    rule = Path(__file__).parents[1] / 'src/mouse_control/udev/71-mouse-control-uaccess.rules'
    content = rule.read_text(encoding='utf-8')
    assert 'MODE="666"' not in content
    assert 'GROUP="input"' not in content
    assert 'ENV{ID_INPUT_MOUSE}=="1"' in content
    assert 'ENV{ID_INPUT_KEYBOARD}!="1"' in content
    assert 'KERNEL=="uinput"' in content
    assert 'SUBSYSTEM=="hidraw"' in content
    assert 'KERNELS=="0003:046D:4074.*"' in content
    assert 'DRIVERS=="logitech-hidpp-device"' in content
    assert 'ATTRS{idVendor}=="046d"' not in content


def test_packaging_declares_gpl_and_installs_udev_rule():
    root = Path(__file__).parents[1]
    metadata = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    spec = (root / 'mouse-control.spec').read_text(encoding='utf-8')
    assert metadata['project']['license'] == {'text': 'GPL-3.0-or-later'}
    assert 'License:        GPL-3.0-or-later' in spec
    assert 'LicenseRef-Proprietary' not in spec
    assert '%{_udevrulesdir}/71-mouse-control-uaccess.rules' in spec
