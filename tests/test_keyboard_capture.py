import sys
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from evdev import InputEvent, ecodes

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from mouse_control import keyboard_capture as capture
from mouse_control.config import generate_config
from mouse_control.discovery import MouseDevice
from mouse_control.remapper import parse_action
from mouse_control.wizard import ButtonCaptureError, ask_for_action, map_mouse_buttons


def event(code, value=1, kind=ecodes.EV_KEY):
    return InputEvent(0, 0, kind, code, value)


def device(fd=10, batches=(), held=()):
    dev = MagicMock()
    dev.fd = fd
    dev.name = "Test keyboard"
    dev.active_keys.return_value = held
    dev.read.side_effect = [BlockingIOError(), *batches]
    dev.capabilities.return_value = {ecodes.EV_KEY: [ecodes.KEY_A]}
    return dev


def run_capture(devices, ready=None, capture_function=None):
    with patch.object(capture, '_open_keyboards', return_value=devices), \
         patch.object(capture, '_capture_terminal', return_value=nullcontext()), \
         patch.object(capture, 'select', side_effect=ready or [(devices[:], [], [])]):
        return (capture_function or capture.capture_keyboard_key)()


def test_chord_capture_holds_keys_until_all_released():
    dev = device(batches=[[
        event(ecodes.KEY_LEFTCTRL), event(ecodes.KEY_LEFTSHIFT), event(ecodes.KEY_S),
        event(ecodes.KEY_S, 2), event(ecodes.KEY_S, 0),
        event(ecodes.KEY_LEFTSHIFT, 0), event(ecodes.KEY_LEFTCTRL, 0)]])
    assert run_capture([dev], capture_function=capture.capture_keyboard_chord) == (
        'chord:KEY_LEFTCTRL+KEY_LEFTSHIFT+KEY_S')
    dev.close.assert_called_once()
    dev.grab.assert_not_called()


def test_chord_capture_ignores_stale_and_one_key_attempts():
    dev = device(held=[ecodes.KEY_ENTER], batches=[[
        event(ecodes.KEY_ENTER), event(ecodes.KEY_ENTER, 0),
        event(ecodes.KEY_A), event(ecodes.KEY_A, 0),
        event(ecodes.KEY_LEFTCTRL), event(ecodes.KEY_C),
        event(ecodes.KEY_C, 0), event(ecodes.KEY_LEFTCTRL, 0)]])
    assert run_capture([dev], capture_function=capture.capture_keyboard_chord) == (
        'chord:KEY_LEFTCTRL+KEY_C')


def test_chord_capture_escape_and_disconnect_close_devices():
    first = device(10, [OSError('unplugged')])
    second = device(11, [[event(ecodes.KEY_ESC)]])
    assert run_capture([first, second], capture_function=capture.capture_keyboard_chord) is None
    first.close.assert_called_once()
    second.close.assert_called_once()


def test_wizard_chord_capture_and_manual_entry(capsys):
    with patch('builtins.input', return_value='6'), \
         patch('mouse_control.wizard.capture_keyboard_chord',
               return_value='chord:KEY_LEFTCTRL+KEY_C'):
        assert ask_for_action('BTN_EXTRA') == 'chord:KEY_LEFTCTRL+KEY_C'
    assert 'Detected keyboard chord' in capsys.readouterr().out
    with patch('builtins.input', side_effect=['6', '7', 'KEY_C',
                                             'KEY_LEFTCTRL++KEY_C', 'key_leftctrl+key_c']), \
         patch('mouse_control.wizard.capture_keyboard_chord', return_value=None):
        assert ask_for_action('BTN_EXTRA') == 'chord:KEY_LEFTCTRL+KEY_C'


@pytest.mark.parametrize('code,name', [(ecodes.KEY_LEFTMETA, 'KEY_LEFTMETA'),
                                      (ecodes.KEY_F12, 'KEY_F12'),
                                      (ecodes.KEY_VOLUMEUP, 'KEY_VOLUMEUP')])
def test_capture_compatible_with_existing_config(code, name):
    dev = device(batches=[[event(code)]])
    assert run_capture([dev]) == name
    assert parse_action(f'key:{name}').code == code
    assert f'key:{name}' in generate_config(MouseDevice('Mouse', '/dev/input/test'),
                                           {'BTN_EXTRA': f'key:{name}'})
    dev.close.assert_called_once()
    dev.grab.assert_not_called()


def test_filters_events_and_menu_enter():
    dev = device(held=[ecodes.KEY_ENTER], batches=[[
        event(ecodes.KEY_ENTER), event(ecodes.KEY_ENTER, 2), event(ecodes.KEY_ENTER, 0),
        event(ecodes.KEY_A, 0), event(ecodes.KEY_A, 2), event(ecodes.BTN_LEFT),
        event(99999), event(0, kind=ecodes.EV_SYN), event(ecodes.KEY_F12)]])
    assert run_capture([dev]) == 'KEY_F12'


def test_queued_enter_is_drained_but_fresh_enter_can_be_bound():
    dev = device()
    dev.read.side_effect = [[event(ecodes.KEY_ENTER)], BlockingIOError(), [event(ecodes.KEY_ENTER)]]
    assert run_capture([dev]) == 'KEY_ENTER'


def test_multiple_keyboards_disconnect_and_cleanup():
    first = device(10, [OSError('unplugged')])
    second = device(11, [[event(ecodes.KEY_VOLUMEUP)]])
    assert run_capture([first, second]) == 'KEY_VOLUMEUP'
    first.close.assert_called_once()
    second.close.assert_called_once()


@pytest.mark.parametrize('failure', [KeyboardInterrupt(), OSError('unplugged')])
def test_cancel_or_disconnect(failure):
    dev = device(batches=[failure])
    assert run_capture([dev]) is None
    dev.close.assert_called_once()


def test_ctrl_c_and_ctrl_alone():
    dev = device(batches=[[event(ecodes.KEY_LEFTCTRL), event(ecodes.KEY_C)]])
    assert run_capture([dev]) is None
    dev = device(batches=[[event(ecodes.KEY_LEFTCTRL), event(ecodes.KEY_LEFTCTRL, 0)]])
    assert run_capture([dev]) == 'KEY_LEFTCTRL'


def test_no_devices():
    assert run_capture([]) is None


def test_aliases_and_unknown_codes():
    with patch.dict(ecodes.bytype[ecodes.EV_KEY], {ecodes.KEY_F12: ['BTN_FAKE', 'KEY_F12']}):
        assert capture.keyboard_key_name(ecodes.KEY_F12) == 'KEY_F12'
    assert capture.keyboard_key_name(ecodes.BTN_LEFT) is None
    assert capture.keyboard_key_name(99999) is None
    assert capture.keyboard_key_name(ecodes.KEY_RESERVED) is None


def test_discovery_prefers_stable_paths_and_skips_duplicates_and_inaccessible():
    paths = ['/dev/input/event1', '/dev/input/by-id/test-event-kbd', '/dev/input/event2',
             '/dev/input/event3', '/dev/input/event4']
    keyboard, mouse, virtual = device(), device(), device()
    mouse.capabilities.return_value = {ecodes.EV_KEY: [ecodes.BTN_LEFT]}
    virtual.name = 'mouse-control: Test'
    def realpath(path):
        return '/dev/input/event1' if path.endswith('event-kbd') else path
    with patch.object(capture, '_iter_candidate_paths', return_value=paths), \
         patch.object(capture.os.path, 'realpath', side_effect=realpath), \
         patch.object(capture, 'InputDevice', side_effect=[keyboard, PermissionError(), mouse, virtual]) as opened:
        assert capture._open_keyboards() == [keyboard]
    assert opened.call_args_list[0].args == ('/dev/input/by-id/test-event-kbd',)
    assert opened.call_count == 4
    mouse.close.assert_called_once()
    virtual.close.assert_called_once()
    keyboard.close.assert_not_called()


def test_terminal_restored_and_buffer_flushed_on_cancel():
    settings = [0, 0, 0, capture.termios.ICANON | capture.termios.ECHO | capture.termios.ISIG, 0, 0, []]
    with patch.object(capture.sys, 'stdin') as stdin, \
         patch.object(capture.termios, 'tcgetattr', side_effect=lambda fd: settings.copy()), \
         patch.object(capture.termios, 'tcsetattr') as setter:
        stdin.isatty.return_value = True
        stdin.fileno.return_value = 0
        with pytest.raises(KeyboardInterrupt), capture._capture_terminal():
            raise KeyboardInterrupt
    assert setter.call_args_list[0].args[2][3] == capture.termios.ISIG
    assert setter.call_args_list[-1].args == (0, capture.termios.TCSAFLUSH, settings)


def test_chord_terminal_allows_ctrl_c_and_restores_signals():
    settings = [0, 0, 0, capture.termios.ICANON | capture.termios.ECHO | capture.termios.ISIG,
                0, 0, []]
    with patch.object(capture.sys, 'stdin') as stdin, \
         patch.object(capture.termios, 'tcgetattr', side_effect=lambda fd: settings.copy()), \
         patch.object(capture.termios, 'tcsetattr') as setter:
        stdin.isatty.return_value = True
        stdin.fileno.return_value = 0
        with capture._capture_terminal(keep_signals=False):
            pass
    assert not setter.call_args_list[0].args[2][3] & capture.termios.ISIG
    assert setter.call_args_list[-1].args == (0, capture.termios.TCSAFLUSH, settings)


def test_wizard_capture_cancel_and_manual_entry(capsys):
    with patch('builtins.input', side_effect=['3']), \
         patch('mouse_control.wizard.capture_keyboard_key', return_value='KEY_LEFTMETA'):
        assert ask_for_action('BTN_EXTRA') == 'key:KEY_LEFTMETA'
    assert 'Detected keyboard key: KEY_LEFTMETA' in capsys.readouterr().out
    with patch('builtins.input', side_effect=['3', '4']), \
         patch('mouse_control.wizard.capture_keyboard_key', return_value=None):
        assert ask_for_action('BTN_EXTRA') == 'disable'
    with patch('builtins.input', side_effect=['5', 'invalid', 'key_f12']):
        assert ask_for_action('BTN_EXTRA') == 'key:KEY_F12'


def test_wizard_offers_canonical_dpi_cycle_action():
    with patch('builtins.input', return_value='8'):
        assert ask_for_action('BTN_FORWARD') == 'dpi-cycle'


def mouse_events(*items, cancel=True):
    for item in items:
        yield item
    if cancel:
        raise KeyboardInterrupt


def run_mouse_mapping(events, actions):
    mouse = MagicMock(name='mouse')
    mouse.name = 'Test mouse'
    mouse.read_loop.return_value = events
    with patch('mouse_control.wizard.InputDevice', return_value=mouse), \
         patch('mouse_control.wizard.ask_for_action', side_effect=actions) as ask:
        result = map_mouse_buttons('/dev/input/test')
    mouse.grab.assert_called_once()
    mouse.ungrab.assert_called_once()
    mouse.close.assert_called_once()
    return result, ask


def test_mouse_grab_failure_is_fatal_and_closes_device(capsys):
    mouse = MagicMock(name='mouse')
    mouse.name = 'Busy mouse'
    mouse.grab.side_effect = OSError(16, 'Device or resource busy')
    with patch('mouse_control.wizard.InputDevice', return_value=mouse), \
         pytest.raises(ButtonCaptureError, match='Could not grab'):
        map_mouse_buttons('/dev/input/test')
    mouse.close.assert_called_once()
    assert 'Device or resource busy' in capsys.readouterr().out


def test_mouse_wizard_configures_two_buttons_sequentially():
    result, ask = run_mouse_mapping(
        mouse_events(event(ecodes.BTN_EXTRA), event(ecodes.BTN_TASK)),
        ['key:KEY_LEFTMETA', 'mouse:BTN_MIDDLE'],
    )
    assert result == {'BTN_EXTRA': 'key:KEY_LEFTMETA', 'BTN_TASK': 'mouse:BTN_MIDDLE'}
    assert [call.args[0] for call in ask.call_args_list] == ['BTN_EXTRA', 'BTN_TASK']


def test_mouse_wizard_updates_same_button_and_ignores_release_noise():
    result, ask = run_mouse_mapping(
        mouse_events(event(ecodes.BTN_EXTRA), event(ecodes.BTN_EXTRA, 0),
                     event(ecodes.BTN_EXTRA)),
        ['passthrough', 'disable'],
    )
    assert result == {'BTN_EXTRA': 'disable'}
    assert ask.call_count == 2


def test_ctrl_c_after_multiple_mouse_mappings_returns_all(capsys):
    result, _ = run_mouse_mapping(
        mouse_events(event(ecodes.BTN_SIDE), event(ecodes.BTN_EXTRA)),
        ['mouse:BTN_MIDDLE', 'passthrough'],
    )
    assert result == {'BTN_SIDE': 'mouse:BTN_MIDDLE', 'BTN_EXTRA': 'passthrough'}
    assert 'Finished button capture.' in capsys.readouterr().out


def test_ctrl_c_in_action_menu_finishes_and_keeps_previous_mappings():
    result, ask = run_mouse_mapping(
        mouse_events(event(ecodes.BTN_SIDE), event(ecodes.BTN_TASK), cancel=False),
        ['passthrough', KeyboardInterrupt()],
    )
    assert result == {'BTN_SIDE': 'passthrough'}
    assert ask.call_count == 2


def test_keyboard_auto_capture_still_works_after_mouse_detection():
    with patch('builtins.input', return_value='3'), \
         patch('mouse_control.wizard.capture_keyboard_key', return_value='KEY_LEFTMETA') as capture_key:
        assert ask_for_action('BTN_EXTRA') == 'key:KEY_LEFTMETA'
    capture_key.assert_called_once()


def test_disable_can_be_selected_after_another_mapping():
    result, _ = run_mouse_mapping(
        mouse_events(event(ecodes.BTN_EXTRA), event(ecodes.BTN_TASK)),
        ['key:KEY_LEFTMETA', 'disable'],
    )
    assert result['BTN_EXTRA'] == 'key:KEY_LEFTMETA'
    assert result['BTN_TASK'] == 'disable'
