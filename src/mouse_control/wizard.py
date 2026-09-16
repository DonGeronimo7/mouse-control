"""Terminal wizard for discovering physical buttons and defining actions."""

from __future__ import annotations

from typing import Optional

from evdev import InputDevice, InputEvent, ecodes

from .keyboard_capture import capture_keyboard_chord, capture_keyboard_key
from .remapper import parse_action


MOUSE_ACTIONS = {
    "1": "passthrough",
    "2": "mouse:BTN_LEFT",
    "3": "mouse:BTN_RIGHT",
    "4": "mouse:BTN_MIDDLE",
    "5": "disable",
}


class ButtonCaptureError(RuntimeError):
    """Raised when the setup wizard cannot capture the physical mouse."""


def get_button_name(code: int) -> str:
    """Resolve a Linux input code to its symbolic name."""
    name = ecodes.bytype.get(ecodes.EV_KEY, {}).get(code)
    if isinstance(name, (list, tuple)):
        name = name[0]
    if isinstance(name, str) and (name.startswith("BTN_") or name.startswith("KEY_")):
        return name
    return f"BTN_{code}"


def wait_for_button_press(device: InputDevice) -> Optional[InputEvent]:
    """Block until a mouse button press is received."""
    try:
        for event in device.read_loop():
            if event.type == ecodes.EV_KEY and event.value == 1:
                return event
    except (KeyboardInterrupt, OSError):
        return None
    return None


def _ask_keyboard_action() -> str:
    while True:
        raw = input("Enter a Linux key code (example KEY_LEFTCTRL): ").strip().upper()
        if raw.startswith("KEY_") and hasattr(ecodes, raw):
            return f"key:{raw}"
        print("Invalid key. Use a Linux KEY_* name, such as KEY_LEFTCTRL or KEY_F13.")


def _ask_chord_action() -> str:
    while True:
        raw = input("Enter key codes joined by + (example KEY_LEFTCTRL+KEY_C): ").strip().upper()
        action = f"chord:{raw}"
        try:
            parse_action(action)
        except ValueError:
            print("Invalid chord. Enter at least two distinct Linux KEY_* names joined by +.")
            continue
        return action


def _ask_mouse_action() -> str:
    while True:
        raw = input("Enter a Linux mouse button (example BTN_MIDDLE): ").strip().upper()
        if raw.startswith("BTN_") and hasattr(ecodes, raw):
            return f"mouse:{raw}"
        print("Invalid mouse button. Use a Linux BTN_* name.")


def ask_for_action(symbolic_name: str) -> str:
    """Ask what the discovered physical button should do."""
    print(f"\nDetected: {symbolic_name}")
    print("  1. Passthrough")
    print("  2. Remap to mouse button")
    print("  3. Remap to keyboard key")
    print("  4. Disable")
    print("  5. Enter a keyboard key code manually")
    print("  6. Remap to keyboard chord")
    print("  7. Enter a keyboard chord manually")
    print("  8. Cycle configured DPI stages")

    while True:
        try:
            choice = input("Action [1]: ").strip() or "1"
        except EOFError:
            print()
            return "passthrough"

        if choice == "1":
            return "passthrough"
        if choice == "2":
            return _ask_mouse_action()
        if choice == "3":
            name = capture_keyboard_key()
            if name is not None:
                print(f"Detected keyboard key: {name}")
                return f"key:{name}"
            continue
        if choice == "5":
            return _ask_keyboard_action()
        if choice == "6":
            action = capture_keyboard_chord()
            if action is not None:
                print(f"Detected keyboard chord: {action.removeprefix('chord:')}")
                return action
            print("You can enter the chord manually with option 7.")
            continue
        if choice == "7":
            return _ask_chord_action()
        if choice == "4":
            return "disable"
        if choice == "8":
            return "dpi-cycle"
        print("Please choose 1, 2, 3, 4, 5, 6, 7, or 8.")


def map_mouse_buttons(device_path: str) -> dict[str, str]:
    """Capture physical button presses and map each to an executable action."""
    try:
        device = InputDevice(device_path)
    except OSError as exc:
        message = f"Error opening mouse: {exc}"
        print(message)
        raise ButtonCaptureError(message) from exc

    print(f"\nOpening: {device.name}")
    print("Press each button you want to configure. Press Ctrl+C when finished.")

    try:
        device.grab()
    except OSError as exc:
        device.close()
        message = f"Could not grab the mouse: {exc}"
        print(message)
        print("Ensure no other process is currently using the input device.")
        raise ButtonCaptureError(message) from exc

    mappings: dict[str, str] = {}
    try:
        for event in device.read_loop():
            if event.type != ecodes.EV_KEY or event.value != 1:
                continue
            name = get_button_name(event.code)
            action = ask_for_action(name)
            mappings[name] = action
            print(f"Registered: {name} -> {action}")
            print("\nPress another button, or Ctrl+C when finished.")
    except KeyboardInterrupt:
        print("\nFinished button capture.")
    finally:
        try:
            device.ungrab()
        except OSError:
            pass
        device.close()
    return mappings
