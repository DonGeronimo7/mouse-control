from pathlib import Path
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from mouse_control.setup_flow import SetupChoices, discover_choices, dpi_screen, polling_screen, review_screen
from mouse_control.discovery import MouseDevice

MOUSE = MouseDevice('Example', '/dev/input/example')


def backend():
    result = Mock()
    result.supports_dpi.return_value = True
    result.get_dpi.return_value = 800
    result.get_dpi_values.return_value = list(range(200, 3201, 50))
    result.set_dpi.side_effect = lambda device, value: value
    result.supports_polling_rate.return_value = True
    result.get_polling_rate.return_value = 500
    result.supports_polling_rate_writes.return_value = True
    result.get_polling_rates.return_value = [125, 500, 1000]
    return result


def test_review_edit_preserves_other_choices_and_retunes_accepted_stage():
    hw = backend()
    choices = SetupChoices(mappings={'BTN_EXTRA': 'disable'})
    discover_choices(hw, MOUSE, choices)
    with patch('builtins.input', side_effect=['2', '1450', '', '']):
        assert dpi_screen(hw, MOUSE, choices) == 'continue'
    assert choices.stages[1] == 1450
    with patch('builtins.input', side_effect=['2']):
        assert review_screen(MOUSE, choices) == '2'
    with patch('builtins.input', side_effect=['2', '']):
        assert polling_screen(choices) == 'continue'
    with patch('builtins.input', side_effect=['2', '1500', 'b', '']):
        assert dpi_screen(hw, MOUSE, choices) == 'continue'
    assert choices.stages[1] == 1450
    assert choices.polling_rate == 500
    assert choices.mappings == {'BTN_EXTRA': 'disable'}
    assert hw.set_dpi.call_args.args[-1] == 1450


def test_cancel_test_restores_accepted_then_wizard_can_restore_original():
    hw = backend()
    choices = SetupChoices()
    discover_choices(hw, MOUSE, choices)
    with patch('builtins.input', side_effect=['1', '850', 'b', 'q']):
        assert dpi_screen(hw, MOUSE, choices) == 'q'
    assert choices.stages[0] == 800
    assert [call.args[-1] for call in hw.set_dpi.call_args_list] == [850, 800]


def test_invalid_dpi_never_reaches_hardware():
    hw = backend()
    choices = SetupChoices()
    discover_choices(hw, MOUSE, choices)
    with patch('builtins.input', side_effect=['1', '825', 'b', '']):
        assert dpi_screen(hw, MOUSE, choices) == 'continue'
    assert all(call.args[-1] != 825 for call in hw.set_dpi.call_args_list)


def test_numeric_tuner_retests_before_blank_acceptance(capsys):
    hw = backend()
    choices = SetupChoices()
    discover_choices(hw, MOUSE, choices)
    with patch('builtins.input', side_effect=['2', '1450', '1400', '', '']):
        assert dpi_screen(hw, MOUSE, choices) == 'continue'
    assert choices.stages[1] == 1400
    assert [call.args[-1] for call in hw.set_dpi.call_args_list] == [1450, 1400]
    out = capsys.readouterr().out
    assert 'Current accepted value: 1500 DPI' in out
    assert 'Supported range: 200–3200 DPI' in out
    assert 'Native increment: 50 DPI' in out
    assert 'L/R' not in out and 'M+' not in out and 'C+' not in out


def test_blank_enter_keeps_accepted_value_without_write():
    hw = backend()
    choices = SetupChoices()
    discover_choices(hw, MOUSE, choices)
    with patch('builtins.input', side_effect=['2', '', '']):
        assert dpi_screen(hw, MOUSE, choices) == 'continue'
    assert choices.stages[1] == 1500
    hw.set_dpi.assert_not_called()


def test_readable_nonwritable_polling_shows_rates_and_current_without_select(capsys):
    hw = backend()
    hw.supports_polling_rate_writes.return_value = False
    choices = SetupChoices()
    discover_choices(hw, MOUSE, choices)
    with patch('builtins.input', side_effect=['2', '']):
        assert polling_screen(choices) == 'continue'
    assert choices.polling_rates == [1000, 500, 125]
    assert choices.current_polling_rate == 500
    assert choices.polling_rate is None
    out = capsys.readouterr().out
    assert '1000 Hz, 500 Hz, 125 Hz' in out
    assert 'Current hardware rate: 500 Hz' in out
    assert '[number] Select' not in out
    assert 'current hardware rate will be kept' in out


def test_writable_polling_defaults_to_max_and_allows_selection(capsys):
    hw = backend()
    choices = SetupChoices()
    discover_choices(hw, MOUSE, choices)
    assert choices.polling_rate == 1000
    with patch('builtins.input', side_effect=['2', '']):
        assert polling_screen(choices) == 'continue'
    assert choices.polling_rate == 500
    assert '[number] Select' in capsys.readouterr().out
