"""Wizard-to-TOML-to-runtime regression tests, using the real config writer."""
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from mouse_control import cli, config
from mouse_control.discovery import MouseDevice
from mouse_control.remapper import DpiCycler

MOUSE = MouseDevice('G305 test', '/dev/input/test', vendor=0x046d, product=0x4074)
FINAL = [800, 1450, 2000, 2400, 3200]


def hardware():
    backend = MagicMock()
    backend.name = 'Test HID'
    backend.get_device_name.return_value = MOUSE.name
    backend.supports_dpi.return_value = True
    backend.get_dpi.return_value = 800
    backend.get_dpi_values.return_value = list(range(200, 12001, 50))
    backend.set_dpi.side_effect = lambda device, value: value
    backend.supports_polling_rate.return_value = False
    backend.supports_polling_rate_writes.return_value = False
    backend.supports_dpi_stages.return_value = False
    return backend


def polling_hardware():
    backend = hardware()
    backend.supports_polling_rate.return_value = True
    backend.supports_polling_rate_writes.return_value = True
    backend.get_polling_rates.return_value = [125, 500, 1000]
    backend.get_polling_rate.return_value = 1000
    return backend


def run_wizard(path, backend, answers, capsys):
    with patch.object(config, 'get_config_path', return_value=path), \
         patch.object(cli, 'get_config_path', return_value=path), \
         patch.object(cli, 'is_service_active', return_value=False), \
         patch.object(cli, 'get_mouse_devices', return_value=[MOUSE]), \
         patch.object(cli, 'select_mouse_device', return_value=MOUSE), \
         patch.object(cli, 'get_backend', return_value=backend), \
         patch.object(cli, 'map_mouse_buttons', return_value={'BTN_TASK': 'dpi-cycle'}), \
         patch('builtins.input', side_effect=answers):
        result = cli.run_setup_wizard()
    return result, capsys.readouterr().out


def test_finish_persists_reviewed_stages_and_runtime_loads_exact_list(tmp_path, capsys):
    path = tmp_path / 'config.toml'
    backend = hardware()
    answers = [
        '', '2', '1450', '', '4', '2400', '', '5', '3200', '', '',
        '', 'n', '',
    ]
    result, output = run_wizard(path, backend, answers, capsys)
    assert result == 0
    assert 'DPI stages: 800 → 1450 → 2000 → 2400 → 3200' in output
    saved = config.load_config(path)
    assert saved['dpi']['stages'] == FINAL
    assert saved['dpi']['active'] == 800
    assert backend.set_dpi.call_args.args[-1] == 800

    runtime_backend = hardware()
    observed = {}
    def exercise_runtime():
        target = observed['cycler'] = remapper.call_args.args[3]
        runtime_backend.get_dpi.return_value = 1450
        observed['cycled'] = target.cycle()
    with patch.object(cli, 'get_mouse_devices', return_value=[MOUSE]), \
         patch.object(cli, 'get_backend', return_value=runtime_backend), \
         patch.object(cli, 'DpiMonitorSupervisor') as monitor, \
         patch.object(cli, 'BatteryMonitorSupervisor'), \
         patch.object(cli, 'MouseRemapper') as remapper:
        remapper.return_value.run.side_effect = exercise_runtime
        assert cli.run_from_config(path) == 0
    cycler = observed['cycler']
    assert isinstance(cycler, DpiCycler)
    assert cycler.stages == FINAL
    assert monitor.call_args.args[3] == FINAL
    assert observed['cycled']
    assert runtime_backend.set_dpi.call_args.args[-1] == 1450
    assert cycler.current_dpi == 1450
    monitor.return_value.notify_dpi.assert_called_once_with(1450)


def test_back_keeps_accepted_stage_and_cancel_does_not_save(tmp_path, capsys):
    path = tmp_path / 'config.toml'
    path.write_text(config.generate_config(MOUSE, {'BTN_TASK': 'dpi-cycle'}))
    original = path.read_bytes()
    backend = hardware()
    answers = ['s', '2', '1450', '', '2', '1500', 'b', 'b', 's', 'q']
    result, output = run_wizard(path, backend, answers, capsys)
    assert result == 0
    assert '2. 1450 DPI' in output
    assert path.read_bytes() == original
    assert backend.set_dpi.call_args.args[-1] == 800


def test_skip_preserves_existing_remaps_and_is_idempotent(tmp_path, capsys):
    path = tmp_path / 'config.toml'
    remaps = {
        'BTN_FORWARD': 'dpi-cycle',
        'BTN_SIDE': 'mouse:BTN_MIDDLE',
        'BTN_EXTRA': 'key:KEY_F13',
        'BTN_TASK': 'chord:KEY_LEFTCTRL+KEY_C',
        'BTN_MIDDLE': 'passthrough',
        'BTN_BACK': 'disable',
    }
    path.write_text(config.generate_config(MOUSE, remaps))
    backend = hardware()

    for _ in range(2):
        result, _ = run_wizard(path, backend, ['s', '', '', 'n', ''], capsys)
        assert result == 0
        assert config.load_config(path)['remap'] == remaps


def test_edit_explicitly_replaces_existing_dpi_cycle_mapping(tmp_path, capsys):
    path = tmp_path / 'config.toml'
    path.write_text(config.generate_config(MOUSE, {'BTN_FORWARD': 'dpi-cycle'}))
    backend = hardware()
    with patch.object(config, 'get_config_path', return_value=path), \
         patch.object(cli, 'get_config_path', return_value=path), \
         patch.object(cli, 'is_service_active', return_value=False), \
         patch.object(cli, 'get_mouse_devices', return_value=[MOUSE]), \
         patch.object(cli, 'select_mouse_device', return_value=MOUSE), \
         patch.object(cli, 'get_backend', return_value=backend), \
         patch.object(cli, 'map_mouse_buttons', return_value={'BTN_FORWARD': 'key:KEY_F13'}), \
         patch('builtins.input', side_effect=['', '', '', 'n', '']):
        assert cli.run_setup_wizard() == 0
    assert config.load_config(path)['remap'] == {'BTN_FORWARD': 'key:KEY_F13'}


def test_setup_does_not_infer_a_dpi_action_for_btn_forward(tmp_path, capsys):
    path = tmp_path / 'config.toml'
    result, _ = run_wizard(path, hardware(), ['s', '', '', 'n', ''], capsys)
    assert result == 0
    assert config.load_config(path)['remap'].get('BTN_FORWARD') is None


def test_no_change_setup_preserves_all_existing_preferences_twice(tmp_path, capsys):
    path = tmp_path / 'config.toml'
    path.write_text('''[device]
name = "G305 test"
event_path = "/dev/input/test"
phys = ""
vendor = 1133
product = 16500

[dpi]
active = 2500
stages = [800, 1500, 2000, 2500, 3000]

[polling]
rate_hz = 500

[notifications]
dpi_changes = false

[remap]
BTN_FORWARD = "dpi-cycle"
BTN_EXTRA = "key:KEY_LEFTMETA"
BTN_SIDE = "chord:KEY_LEFTCTRL+KEY_LEFTSHIFT+KEY_S"

[future]
retain_me = "yes"

[future.nested]
also_retain_me = 7
''')
    expected = config.load_config(path)
    backend = polling_hardware()
    for _ in range(2):
        result, output = run_wizard(path, backend, ['s', '', '', 'n', ''], capsys)
        assert result == 0
        assert config.load_config(path) == expected
        assert 'Configured rate: 500 Hz' in output
        assert 'Current hardware rate: 1000 Hz' in output
    backend.set_dpi.assert_not_called()
    backend.set_polling_rate.assert_not_called()


def test_isolated_dpi_edit_preserves_polling_active_and_remaps(tmp_path, capsys):
    path = tmp_path / 'config.toml'
    remaps = {'BTN_FORWARD': 'dpi-cycle', 'BTN_EXTRA': 'key:KEY_LEFTMETA'}
    path.write_text(config.generate_config(MOUSE, remaps, [800, 1500, 2000, 2500, 3000],
                                            active_dpi=2500, polling_rate_hz=500))
    result, _ = run_wizard(path, polling_hardware(),
                           ['s', '2', '1450', '', '', '', 'n', ''], capsys)
    assert result == 0
    saved = config.load_config(path)
    assert saved['dpi'] == {'active': 2500, 'stages': [800, 1450, 2000, 2500, 3000]}
    assert saved['polling']['rate_hz'] == 500
    assert saved['remap'] == remaps


def test_isolated_polling_edit_preserves_dpi_and_remaps(tmp_path, capsys):
    path = tmp_path / 'config.toml'
    remaps = {'BTN_FORWARD': 'dpi-cycle', 'BTN_EXTRA': 'key:KEY_LEFTMETA'}
    path.write_text(config.generate_config(MOUSE, remaps, [800, 1500, 2000, 2500, 3000],
                                            active_dpi=2500, polling_rate_hz=500))
    backend = polling_hardware()
    result, _ = run_wizard(path, backend, ['s', '', '3', '', 'n', ''], capsys)
    assert result == 0
    saved = config.load_config(path)
    assert saved['dpi'] == {'active': 2500, 'stages': [800, 1500, 2000, 2500, 3000]}
    assert saved['polling']['rate_hz'] == 125
    assert saved['remap'] == remaps
    backend.set_dpi.assert_not_called()
    backend.set_polling_rate.assert_called_once_with(MOUSE, 125)


def test_isolated_remap_edit_preserves_dpi_and_polling(tmp_path, capsys):
    path = tmp_path / 'config.toml'
    remaps = {'BTN_FORWARD': 'dpi-cycle', 'BTN_EXTRA': 'key:KEY_LEFTMETA'}
    path.write_text(config.generate_config(MOUSE, remaps, [800, 1500, 2000, 2500, 3000],
                                            active_dpi=2500, polling_rate_hz=500))
    backend = polling_hardware()
    with patch.object(config, 'get_config_path', return_value=path), \
         patch.object(cli, 'get_config_path', return_value=path), \
         patch.object(cli, 'is_service_active', return_value=False), \
         patch.object(cli, 'get_mouse_devices', return_value=[MOUSE]), \
         patch.object(cli, 'select_mouse_device', return_value=MOUSE), \
         patch.object(cli, 'get_backend', return_value=backend), \
         patch.object(cli, 'map_mouse_buttons', return_value={'BTN_EXTRA': 'disable'}), \
         patch('builtins.input', side_effect=['', '', '', 'n', '']):
        assert cli.run_setup_wizard() == 0
    saved = config.load_config(path)
    assert saved['dpi'] == {'active': 2500, 'stages': [800, 1500, 2000, 2500, 3000]}
    assert saved['polling']['rate_hz'] == 500
    assert saved['remap'] == {'BTN_FORWARD': 'dpi-cycle', 'BTN_EXTRA': 'disable'}
    backend.set_dpi.assert_not_called()
    backend.set_polling_rate.assert_not_called()
