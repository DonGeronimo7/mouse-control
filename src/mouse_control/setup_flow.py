"""Revisitable, backend-neutral setup choices. Hardware writes here are temporary."""
from __future__ import annotations
from dataclasses import dataclass, field
from .config import DEFAULT_DPI, DEFAULT_DPI_STAGES
from .hardware import HardwareError


@dataclass
class SetupChoices:
    stages: list[int] = field(default_factory=lambda: list(DEFAULT_DPI_STAGES))
    active_dpi: int = DEFAULT_DPI
    polling_rate: int | None = None
    mappings: dict[str, str] = field(default_factory=dict)
    enable_service: bool = True
    original_dpi: int | None = None
    dpi_values: list[int] = field(default_factory=list)
    polling_rates: list[int] = field(default_factory=list)
    dpi_writable: bool = False
    dpi_increment: int | None = None
    polling_readable: bool = False
    polling_writable: bool = False
    current_polling_rate: int | None = None
    dpi_changed: bool = False
    polling_changed: bool = False


def _dpi_number(value):
    if value is None:
        return None
    if hasattr(value, 'display_value'):
        return int(value.display_value)
    if isinstance(value, tuple):
        return int(value[0])
    return int(value)


def discover_choices(backend, device, choices):
    try:
        choices.dpi_writable = bool(backend.supports_dpi(device))
        if choices.dpi_writable:
            choices.dpi_values = sorted(set(backend.get_dpi_values(device)))
            if len(choices.dpi_values) > 1:
                increments = {b - a for a, b in zip(choices.dpi_values, choices.dpi_values[1:])}
                if len(increments) == 1:
                    choices.dpi_increment = increments.pop()
            choices.original_dpi = _dpi_number(backend.get_dpi(device))
            if choices.dpi_values:
                unsupported = [stage for stage in choices.stages if stage not in choices.dpi_values]
                if unsupported:
                    print('Some configured DPI stages are not currently reported by the mouse; '
                          'they will be preserved unless you edit them.')
    except (HardwareError, TypeError, ValueError) as exc:
        print(f'DPI tuning unavailable: {exc}')
        choices.dpi_writable = False
    try:
        choices.polling_readable = bool(backend.supports_polling_rate(device))
        choices.polling_writable = bool(backend.supports_polling_rate_writes(device))
        if choices.polling_readable:
            choices.polling_rates = sorted(set(backend.get_polling_rates(device)), reverse=True)
            choices.current_polling_rate = backend.get_polling_rate(device)
        if choices.polling_writable and choices.polling_rates and choices.polling_rate is None:
            # There is no persisted preference for this field, so the legacy
            # setup default remains appropriate.  Existing values are never
            # replaced by discovery.
            choices.polling_rate = choices.polling_rates[0]
    except HardwareError as exc:
        print(f'Polling capability query unavailable: {exc}')
        choices.polling_writable = False
        choices.polling_rate = None


def restore_dpi(backend, device, value):
    if value is None:
        return
    try:
        backend.set_dpi(device, value)
    except HardwareError as exc:
        print(f'Could not restore DPI: {exc}')


def tune_stage(backend, device, choices, index):
    """Return accepted/back/cancel; only a confirmed write updates wizard state."""
    accepted = choices.stages[index]
    current = accepted
    values = choices.dpi_values
    if not values or accepted not in values:
        print('This stage cannot be live-tuned: reported DPI values are unavailable or exclude it.')
        return 'back'
    tested = False
    while True:
        print(f'\nDPI stage {index + 1}/{len(choices.stages)}')
        print(f'Current accepted value: {accepted} DPI')
        print(f'Supported range: {values[0]}–{values[-1]} DPI')
        print(f'Native increment: {choices.dpi_increment} DPI' if choices.dpi_increment else
              'Native increment: varies or was not reported')
        print('[B] Back (discard test)  [Q] Cancel setup')
        prompt = (f'Enter another DPI to keep testing, or press Enter to accept {current}: '
                  if tested else f'Enter a DPI to test, or press Enter to keep {current}: ')
        raw = input(prompt).strip().lower()
        if raw == '':
            choices.stages[index] = current
            if index == 0:
                choices.active_dpi = current
            choices.dpi_changed = True
            return 'accept'
        if raw in ('b', 'esc'):
            restore_dpi(backend, device, accepted)
            return 'back'
        if raw == 'q':
            restore_dpi(backend, device, accepted)
            return 'cancel'
        try:
            requested = int(raw)
        except ValueError:
            print('Enter a supported DPI value, B, or Q.')
            continue
        if requested not in values:
            print(f'{requested} DPI is unsupported. Range: {values[0]}–{values[-1]} DPI.')
            continue
        try:
            result = backend.set_dpi(device, requested)
            confirmed = _dpi_number(result) if result is not None else _dpi_number(backend.get_dpi(device))
            if confirmed != requested:
                print(f'Mouse reported {confirmed} DPI; requested value was not accepted.')
                continue
            current = confirmed
            tested = True
            print(f'Testing {current} DPI. Move the mouse to check the feel.')
        except HardwareError as exc:
            print(f'Could not test DPI: {exc}')


def dpi_screen(backend, device, choices):
    while True:
        print('\nDPI configuration')
        for i, value in enumerate(choices.stages, 1):
            print(f'  {i}. {value} DPI')
        print('[number] Test/change stage  [Enter] Continue  [B] Back  [Q] Cancel setup')
        raw = input('> ').strip().lower()
        if raw == '':
            return 'continue'
        if raw in ('b', 'q'):
            return raw
        if raw.isdigit() and 1 <= int(raw) <= len(choices.stages):
            if not choices.dpi_writable:
                print('Live DPI tuning is unavailable for this mouse.')
                continue
            result = tune_stage(backend, device, choices, int(raw) - 1)
            if result == 'cancel':
                return 'q'
            continue
        print('Choose a stage number, Enter, B, or Q.')


def polling_screen(choices):
    while True:
        print('\nPolling rate')
        if choices.polling_rates:
            print('Hardware-reported supported rates: ' +
                  ', '.join(f'{hz} Hz' for hz in choices.polling_rates))
        else:
            print('Supported rates were not reported by the mouse.')
        if choices.current_polling_rate is not None:
            print(f'Current hardware rate: {choices.current_polling_rate} Hz')
        if choices.polling_rate is not None:
            print(f'Configured rate: {choices.polling_rate} Hz')
        if choices.polling_writable and choices.polling_rates:
            for i, hz in enumerate(choices.polling_rates, 1):
                print(f'  {i}. {hz} Hz' + (' [selected]' if hz == choices.polling_rate else ''))
            print(f'[Enter] Continue with configured {choices.polling_rate} Hz  [number] Select  '
                  '[B] Back  [Q] Cancel setup')
        else:
            print('Report-rate changes are unavailable; the current hardware rate will be kept.')
            print('[Enter] Continue  [B] Back  [Q] Cancel setup')
        raw = input('> ').strip().lower()
        if raw == '':
            return 'continue'
        if raw in ('b', 'q'):
            return raw
        if (choices.polling_writable and raw.isdigit() and
                1 <= int(raw) <= len(choices.polling_rates)):
            choices.polling_rate = choices.polling_rates[int(raw) - 1]
            choices.polling_changed = True
            continue
        print('Choose a listed rate, Enter, B, or Q.' if choices.polling_writable
              else 'Choose Enter, B, or Q.')


def review_screen(device, choices):
    print('\nMouse configuration')
    print(f'Device: {device.name}')
    print('DPI stages: ' + ' → '.join(map(str, choices.stages)))
    print(f'Polling rate: {choices.polling_rate} Hz' if choices.polling_rate else
          f'Polling rate: unchanged ({choices.current_polling_rate} Hz current)'
          if choices.current_polling_rate is not None else 'Polling rate: unchanged')
    print(f'Buttons: {len(choices.mappings)} mappings')
    print('[1] Edit DPI  [2] Edit polling  [3] Edit buttons')
    print('[Enter] Finish  [B] Back  [Q] Cancel setup')
    while True:
        raw = input('> ').strip().lower()
        if raw in ('', 'b', 'q', '1', '2', '3'):
            return raw
        print('Choose Enter, B, Q, 1, 2, or 3.')